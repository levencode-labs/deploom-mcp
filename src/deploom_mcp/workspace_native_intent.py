"""Snapshot-bound manifest intent for explicitly authorized npm upgrades."""
from __future__ import annotations

import base64
import binascii
import json
import os
import re

from semantic_version import NpmSpec, Version

from deploom_mcp.dependency_audit import AuditError
from deploom_mcp.goal_contract import GoalContractError, canonical_digest
from deploom_mcp.workspace_goal_request import validate_goal_request
from deploom_mcp.workspace_inventory import observe_npm_workspace
from deploom_mcp.workspace_plan_details import root_peer_evidence
from deploom_mcp.workspace_plan_snapshot import _requested_registry, _workspace_registry


_PACKAGE_NAME = re.compile(r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*")


def _baseline_version(record, name, declared, target, registry):
    if (record.get("link") or record.get("optional") or record.get("name", name) != name):
        raise GoalContractError("NATIVE_INTENT_BASELINE_UNPROVEN")
    version = record.get("version")
    try:
        current = Version(version)
        if current.prerelease or current.build or not NpmSpec(declared).match(current):
            raise ValueError
    except (TypeError, ValueError):
        raise GoalContractError("NATIVE_INTENT_BASELINE_UNPROVEN") from None
    if Version(target) < current or declared == target:
        raise GoalContractError("NATIVE_INTENT_REQUIRES_UPGRADE")
    integrity = record.get("integrity")
    filename = f"{name.rsplit('/', 1)[-1]}-{version}.tgz"
    registries = (registry, "https://registry.npmjs.org")
    resolved = record.get("resolved")
    if (not isinstance(integrity, str) or not integrity.startswith("sha512-")
            or not isinstance(resolved, str)
            or resolved not in {f"{base}/{name}/-/{filename}" for base in registries}):
        raise GoalContractError("NATIVE_INTENT_BASELINE_ARTIFACT_UNPROVEN")
    try:
        if len(base64.b64decode(integrity[7:], validate=True)) != 64:
            raise ValueError
    except (ValueError, binascii.Error):
        raise GoalContractError("NATIVE_INTENT_BASELINE_ARTIFACT_UNPROVEN") from None
    return version


def prepare_native_intent(project, request, resolution_slots, *, render, save, root_peer_checker=None):
    goal = {key: value for key, value in request.items()
            if key not in {"responseMode", "migrationCard", "compactEdits", "editsStrategy"}}
    validate_goal_request(goal, allow_additions=True)
    if "candidateLimit" in goal:
        raise GoalContractError("NATIVE_INTENT_REQUIRES_EXACT_TARGETS")
    targets = {}
    for row in goal["scope"]:
        selector = row["selector"]
        version = row.get("exactTarget")
        if (set(selector) != {"owner", "section", "name"}
                or selector["section"] not in {"dependencies", "devDependencies"} or version is None):
            raise GoalContractError("NATIVE_INTENT_REQUIRES_EXACT_TARGETS")
        key = tuple(selector[field] for field in ("owner", "section", "name"))
        if key in targets:
            raise GoalContractError("DUPLICATE_WORKSPACE_SELECTOR")
        adding = row.get("operation") == "ADD"
        if adding and not _PACKAGE_NAME.fullmatch(selector["name"]):
            raise GoalContractError("INVALID_WORKSPACE_SELECTOR")
        targets[key] = (version, adding)
    requested = _requested_registry(goal, online_metadata=True)
    configured = os.environ.get("DEPLOOM_WORKSPACE_NPM_REGISTRY")
    if not configured:
        raise GoalContractError("WORKSPACE_REQUEST_REGISTRY_NOT_AUTHORIZED")
    def bind(digest, declarations, packages, inputs):
        try:
            registry = _workspace_registry(configured, inputs, requested, native_intent=True)
        except AuditError:
            raise GoalContractError("WORKSPACE_REQUEST_REGISTRY_NOT_AUTHORIZED") from None
        manifests = {path: json.loads(raw.decode("utf-8-sig"))
                     for path, raw in inputs.items() if path.endswith("package.json")}
        if any(manifest.get("overrides") not in (None, {}) for manifest in manifests.values()):
            raise GoalContractError("NATIVE_INTENT_OVERRIDES_UNSUPPORTED")
        declared_specs = {(entry["owner"], entry["section"], entry["name"]): entry["declaredSpec"]
                          for entry in declarations}
        changes, card_targets, grouped = [], [], {}
        has_upgrade = False
        updated_count = 0
        declared_names = {(owner, name) for owner, _, name in declared_specs}
        for (owner, section, name), (version, adding) in sorted(targets.items()):
            key = (owner, section, name)
            manifest = "package.json" if owner == "." else f"{owner}/package.json"
            if adding:
                if (owner, name) in declared_names:
                    raise GoalContractError("NATIVE_INTENT_ADD_ALREADY_DECLARED")
                if not isinstance(manifests.get(manifest, {}).get(section), dict):
                    raise GoalContractError("NATIVE_INTENT_ADD_SECTION_NOT_FOUND")
                declared = None
                declared_names.add((owner, name))
            else:
                if key not in declared_specs:
                    raise GoalContractError("WORKSPACE_SCOPE_NOT_FOUND")
                slot = next((slot for slot in resolution_slots(name, "" if owner == "." else owner)
                             if slot in packages), None)
                declared = declared_specs[key]
                baseline = _baseline_version(packages.get(slot, {}), name, declared, version, registry)
                has_upgrade |= baseline != version
                updated_count += 1
                card_targets.append({"declaration": list(key), "baselineLock": [slot, baseline],
                                     "selectedVersion": version})
            change = {"owner": owner, "section": section, "name": name,
                      "fromSpec": declared, "toVersion": version}
            grouped.setdefault(manifest, []).append(change)
            changes.append(change)
        if not has_upgrade:
            raise GoalContractError("NATIVE_INTENT_REQUIRES_UPGRADE")
        conflicts = []
        if root_peer_checker is not None:
            root_targets = {name: version for (owner, _, name), (version, _) in targets.items() if owner == "."}
            conflicts = root_peer_checker(packages, root_targets)
        patch = None
        if not conflicts:
            patch = b"".join(render(path, inputs[path], rows) for path, rows in sorted(grouped.items()))
        return digest, len(declarations) - updated_count, changes, card_targets, patch, conflicts

    digest, held, changes, card_targets, patch, conflicts = observe_npm_workspace(
        project, plan_snapshot=bind, allow_root_only=True,
    )
    request_digest = canonical_digest(request)
    if conflicts:
        diagnostics = root_peer_evidence({"phase": "BASELINE_ROOT_PEER_CHECK",
                                         "conflictCount": len(conflicts), "heldConflicts": conflicts})
        return {"ok": False, "error": {"code": "HELD_LOCK_PROVIDER_CHANGED", **diagnostics},
                "inventoryDigest": digest, "requestDigest": request_digest, "executionReady": False}
    artifact = save(project, patch)
    response = {
        "ok": True, "schemaVersion": 1, "kind": "npm-workspace-native-manifest-proposal",
        "inventoryDigest": digest, "requestDigest": request_digest,
        "authority": "MANIFEST_INTENT_ONLY", "verification": "NOT_VERIFIED", "executionReady": False,
        "jointAssignment": "NOT_COMPUTED", "packageManagerValidation": {"status": "REQUIRED"},
        "action": {
            "kind": "STAGE_MANIFEST_PATCH_FOR_NATIVE_VERIFICATION",
            "argv": ["deploom-workspace", "apply", str(project), digest, artifact["path"], artifact["sha256"]],
        },
        "heldDeclarationCount": held, "patch": artifact,
    }
    if len(changes) == 1:
        response["target"] = changes[0]
    else:
        response["targets"] = changes
    if request.get("migrationCard"):
        from deploom_mcp.workspace_migration_card import describe_exact_migrations

        response["migrationCard"] = describe_exact_migrations(project, card_targets)
    return response
