"""Advisory project observations for the exact transitions selected by EDITS."""
from __future__ import annotations

import os
import re
import time
from collections import defaultdict
from pathlib import Path, PurePosixPath

from semantic_version import Version

from deploom_mcp.goal_execution_registry import configured_state_root
from deploom_mcp.host_evidence_policy import external_network_evidence_disabled, mirror_knowledge_only
from deploom_mcp.knowledge_contract_v2 import PKG, VERSION
from deploom_mcp.migration_release import PUBLIC_NPM, collect_workspace_releases
from deploom_mcp.shared_knowledge_batch import _private_package, lookup_batch
from deploom_mcp.shared_records import directory as knowledge_directory
from deploom_mcp.migration_usage import inspect_usage
from deploom_mcp.workspace_inventory import _read_bytes

_SCRIPT = r"\.[cm]?[jt]s"
_CONFIGS = {
    "typescript": (re.compile(r"tsconfig(?:\.[a-zA-Z0-9_-]+)*\.json"), ("compilerOptions.baseUrl",)),
    "vite": (re.compile(r"vite\.config" + _SCRIPT), ()),
    "vitest": (re.compile(r"(?:vitest|vite)\.config" + _SCRIPT), ("test.poolOptions",)),
    "eslint": (re.compile(r"eslint\.config" + _SCRIPT), ()),
    "jest": (re.compile(r"jest\.config(?:" + _SCRIPT + r"|\.json)"), ()),
}

# One rule is a version threshold and a positive syntax selector. Findings
# outside these selectors still identify files for manual review.
_RULES = {
    "typescript": (
        ("6.0.0", "typescript-base-url", "key", ("compilerOptions.baseUrl",),
         "Review the deprecated baseUrl option. Preserve module resolution with explicit relative paths or paths mappings.",
         "Run the existing typecheck in each affected workspace and verify imports using the old baseUrl.",
         "https://www.typescriptlang.org/docs/handbook/release-notes/typescript-6-0.html#deprecated---baseurl"),
    ),
    "react-dom": (
        ("19.0.0", "react-dom-legacy-apis", "symbol", ("render", "hydrate", "unmountComponentAtNode", "findDOMNode"),
         "Check the observed react-dom API against the React 19 replacements before editing its callers.",
         "Verify mount, hydration or unmount behavior for the changed entry point.",
         "https://react.dev/blog/2024/04/25/react-19-upgrade-guide#removed-react-dom-apis"),
    ),
    "vite": (
        ("7.0.0", "vite-split-vendor-chunks", "symbol", ("splitVendorChunkPlugin",),
         "Remove the legacy plugin or preserve its intended chunking with explicit manualChunks.",
         "Compare production chunk boundaries and load a representative page.",
         "https://v7.vite.dev/guide/migration#removed-deprecated-features"),
        ("7.0.0", "vite-sass-legacy-api", "value", ("legacy",),
         "Update the observed Sass API setting and review the stylesheet integration for Vite 7.",
         "Build and compare a representative Sass stylesheet.",
         "https://v7.vite.dev/guide/migration"),
    ),
    "vitest": (
        ("4.0.0", "vitest-pool-options", "key", ("test.poolOptions",),
         "Review the observed poolOptions configuration against the Vitest 4 pool options.",
         "Run the existing tests that depend on the pool or worker isolation settings.",
         "https://v4.vitest.dev/guide/migration#pool-rework"),
    ),
    "strip-json-comments": (
        ("4.0.0", "strip-json-comments-module-format", "form", ("REQUIRE_LITERAL", "IMPORT_EQUALS_REQUIRE"),
         "Check how the require site handles the ESM default export with the project's Node version.",
         "Run the existing caller against JSON comments and trailing comma fixtures.",
         "https://github.com/sindresorhus/strip-json-comments/releases/tag/v4.0.0"),
    ),
}

def _config_files(project: Path, pairs, deadline):
    """Report literal config filenames even when the pinned parser is unavailable."""
    rows = []
    for package, owner in pairs:
        if package not in _CONFIGS or time.monotonic() >= deadline:
            continue
        filenames = {
            "typescript": ("tsconfig.json", "tsconfig.build.json"),
            "vite": ("vite.config.ts", "vite.config.js", "vite.config.mts", "vite.config.mjs"),
            "vitest": ("vitest.config.ts", "vitest.config.js", "vite.config.ts", "vite.config.js"),
            "eslint": ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", ".eslintrc", ".eslintrc.json"),
            "jest": ("jest.config.js", "jest.config.ts", "jest.config.cjs", "jest.config.mjs", "jest.config.json"),
        }[package]
        for name in filenames:
            path = project / owner / name
            try:
                _read_bytes(path, 131072, root=project)
            except (OSError, ValueError):
                continue
            rows.append({"package": package, "path": path.relative_to(project).as_posix(), "form": "CONFIG_FILE"})
    return rows

def _signals(rows, before, after, package):
    changes = []
    for release, identifier, field, selectors, action, check, url in _RULES.get(package, ()):
        if not before < Version(release) <= after:
            continue
        matches = []
        for index, finding in enumerate(rows):
            if finding.get("typeOnly"):
                continue
            if field == "symbol":
                if finding.get("specifier") != package:
                    continue
                values = finding.get("knownImportedSymbols", ())
            else:
                values = (finding.get(field),)
            if any(value in selectors for value in values):
                matches.append(index)
        if matches:
            changes.append({
                "id": identifier, "status": "STATIC_MATCH_REVIEW_REQUIRED",
                "matchedPatterns": matches, "source": url, "sourceAuthority": "OFFICIAL_REFERENCE",
                "suggestedAction": action,
                "suggestedCheck": {"status": "NOT_RUN", "instruction": check},
            })
    return changes

def _reference_mentions(transition, reference):
    symbols = {
        symbol
        for finding in transition["matchedPatterns"]
        for symbol in (finding.get("key"), finding.get("binary"), *finding.get("knownImportedSymbols", ()))
        if symbol
    }
    hints = []
    for document in reference.get("documents", []):
        texts = " ".join(document["excerpts"])
        mentioned = sorted(symbol for symbol in symbols if f"`{symbol}`" in texts)
        if mentioned:
            hints.append({"status": "SOURCE_MENTION_REVIEW_REQUIRED",
                          "symbols": mentioned[:8], "source": document["source"]["url"]})
    return hints


def _transition_evidence(project, state, transitions, integrities, registry, mode):
    if not transitions:
        return {}
    queries = [{"package": row["package"], "from_version": row["fromVersion"],
                "to_version": row["toVersion"], "package_manager": "npm"} for row in transitions
               if PKG.fullmatch(row["package"]) and VERSION.fullmatch(row["fromVersion"])
               and VERSION.fullmatch(row["toVersion"])]
    lookup = lookup_batch(knowledge_directory(), queries) if queries else {}
    signed = {(row["package"], row["from_version"], row["to_version"]): row
              for row in lookup.get("results", [])}
    offline = (mode != "ON_DEMAND" or external_network_evidence_disabled() or mirror_knowledge_only()
               or os.environ.get("DEPLOOM_MCP_MIGRATION_OFFLINE") == "1")
    pending = []
    for row in transitions:
        key = (row["package"], row["fromVersion"], row["toVersion"])
        matched = signed.get(key, {})
        records = matched.get("knowledge", [])
        row["evidence"] = {
            "signedKnowledge": {
                "status": matched.get("status", lookup.get("status", "UNAVAILABLE")),
                "snapshotRevision": None if "snapshotSha256" in matched else lookup.get("snapshotRevision"),
                **{key: matched[key] for key in ("snapshotSource", "snapshotSha256") if key in matched},
                "matchingCount": matched.get("matchingCount", 0),
                "matchingRecordCount": matched.get("matchingRecordCount", matched.get("matchingCount", 0)),
                "deduplicatedCount": matched.get("deduplicatedCount", 0),
                "omittedCount": max(0, matched.get("matchingCount", 0) - len(records)),
                "records": [{**{k: record[k] for k in ("id", "kind", "title", "summary", "matchStrength",
                                                       "sourceCommit", "evidence", "testedTransition", "evidenceScope",
                                                       "relatedRecordCount", "relatedRecordIds")},
                             **{k: record[k] for k in ("conditions", "actions", "verification", "negativeControls")},
                             "recipientApplicability": "UNKNOWN",
                             "applicabilityReason": "CONDITIONS_NOT_MACHINE_VERIFIED"}
                            for record in records],
            },
            "official": {"status": "UNAVAILABLE"},
        }
        evidence = row["evidence"]
        if _private_package(key[0]) or registry != PUBLIC_NPM:
            evidence["official"]["reason"] = "PUBLIC_ROUTE_NOT_AUTHORIZED"
            continue
        if len(integrities.get(key, ())) != 1 or None in integrities[key]:
            evidence["official"]["reason"] = "PUBLIC_BASELINE_UNPROVEN"
            continue
        if Version(key[1]) >= Version(key[2]) or Version(key[2]).prerelease or Version(key[2]).build:
            evidence["official"]["reason"] = "STABLE_FORWARD_TRANSITION_REQUIRED"
            continue
        if len(pending) >= 2:
            evidence["official"]["reason"] = "OFFICIAL_TRANSITION_LIMIT"
            continue
        pending.append((row, (*key, next(iter(integrities[key])))))
    no_network = {"mode": "CACHE_ONLY" if offline else "ON_DEMAND", "httpTransactions": 0}
    if not pending:
        return no_network
    try:
        local, checkout = state.resolve(), project.resolve()
        if local.is_relative_to(checkout) or checkout.is_relative_to(local):
            for row, _ in pending:
                row["evidence"]["official"]["reason"] = "EVIDENCE_STATE_OVERLAP_REJECTED"
            return no_network
        batch = collect_workspace_releases(state, [query for _, query in pending], offline=offline)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError):
        for row, _ in pending:
            row["evidence"]["official"]["reason"] = "OFFICIAL_EVIDENCE_UNAVAILABLE"
        return no_network if offline else {**no_network, "httpTransactions": None}
    for (row, _), reference in zip(pending, batch["rows"], strict=True):
        row["evidence"]["official"] = reference
        mentions = _reference_mentions(row, reference)
        if mentions:
            row["officialPatternHints"] = mentions
    return batch["metrics"]


def build_project_impact(project: Path, targets: list[dict], owners: list[str], inventory_digest: str,
                         *, registry=None, evidence_mode="CACHE_ONLY") -> dict:
    # Bound response and parsing work independently of the size of the solver assignment.
    transitions = defaultdict(set)
    integrities = defaultdict(set)
    scripts = {}
    unresolved = 0
    for row in targets:
        owner, _, package = row["declaration"]
        locked = row["baselineLock"]
        before, after = locked[1] if locked else None, row["selectedVersion"]
        if before == after:
            continue
        try:
            old, new = Version(before), Version(after)
        except (TypeError, ValueError):
            unresolved += 1
            continue
        transitions[package, old, new].add(owner)
        integrities[package, str(old), str(new)].add(row.get("publicNpmIntegrity"))
        if "scriptPatterns" in row:
            scripts[package, old, new, owner] = row
    selected = list(transitions.items())[:8]
    result = {"authority": "RECOMMENDATION_ONLY", "coverage": None, "transitions": [],
              "inventoryDigest": inventory_digest, "omittedTransitions": len(transitions) - len(selected),
              "unresolvedTransitions": unresolved}
    if not selected:
        return result
    packages = sorted({package for (package, _, _), _ in selected})
    state = configured_state_root()
    deadline = time.monotonic() + 5
    coverage = inspect_usage(project, packages, state, deadline=deadline, source_identity=inventory_digest,
                             config_keys=[(pattern, package, keys) for package, (pattern, keys) in _CONFIGS.items()
                                          if package in packages])
    findings = coverage["observations"]
    if coverage["localCoverage"] is None and coverage["reason"] == "LOCAL_INSPECTION_UNAVAILABLE":
        findings = _config_files(project, sorted({(package, owner) for (package, _, _), group in selected for owner in group}), deadline)
    result["coverage"] = {"status": coverage["status"], "reason": coverage["reason"],
                          "files": coverage["localCoverage"], "fileManifestDigest": coverage.get("fileManifestDigest")}
    known_owners = sorted(
        ((PurePosixPath(owner).parts, owner) for owner in set(owners) - {"."}),
        key=lambda item: (-len(item[0]), item[1]),
    )
    observations_by_package = defaultdict(list)
    for finding in findings:
        parts = PurePosixPath(finding["path"]).parts
        owner = next((owner for prefix, owner in known_owners if parts[:len(prefix)] == prefix), ".")
        observations_by_package[finding["package"]].append({
            **{k: v for k, v in finding.items() if k not in {"package", "semanticApplicability"}},
            "workspace": owner,
        })

    for (package, before, after), declarations in selected:
        current = observations_by_package[package]
        owners_with_scripts = [(owner, scripts[package, before, after, owner]) for owner in sorted(declarations)
                               if (package, before, after, owner) in scripts]
        command_matches = [
            {**pattern, "path": (PurePosixPath(owner) / "package.json").as_posix(), "workspace": owner,
             "authority": "OBSERVED_SYNTAX_ONLY", "bindingOrRuntimeResolution": "UNKNOWN"}
            for owner, row in owners_with_scripts for pattern in row["scriptPatterns"]
        ]
        if command_matches and result["coverage"]["status"] == "NOT_OBSERVED_SCOPED":
            result["coverage"].update(status="OBSERVED_SYNTAX_ONLY", reason="OBSERVED_NPM_SCRIPT_COMMAND_HEAD")
        matching_owner = [row for row in current if row["workspace"] in declarations]
        rows = matching_owner[:12] + command_matches[:4]
        outside_owners = sorted({row["workspace"] for row in current if row["workspace"] not in declarations})
        changes = _signals(rows, before, after, package)
        render_symbols = {"render", "hydrate", "unmountComponentAtNode"}
        codemods = []
        if any(change["id"] == "react-dom-legacy-apis" and any(
            render_symbols.intersection(rows[index]["knownImportedSymbols"])
            for index in change["matchedPatterns"]
        ) for change in changes):
            codemods.append({
                "id": "react/19/replace-reactdom-render", "status": "REVIEW_REQUIRED",
                "source": "https://react.dev/blog/2024/04/25/react-19-upgrade-guide#removed-reactdomrender",
                "verification": "Review the transform against the observed call sites, inspect the diff, and verify mount, hydration and unmount as applicable.",
            })
        unknowns = ["EFFECTIVE_CONFIG_AND_MODULE_RESOLUTION", "CROSS_WORKSPACE_VERSION_ATTRIBUTION"]
        if owners_with_scripts:
            unknowns.append("NPM_SCRIPT_SHELL_AND_COMMAND_RESOLUTION")
        if len(command_matches) > 4 or any(row["scriptPatternsLimited"] for _, row in owners_with_scripts):
            unknowns.append("NPM_SCRIPT_SCAN_LIMIT")
        result["transitions"].append({
            "package": package, "fromVersion": str(before), "toVersion": str(after),
            "dependencyOwners": sorted(declarations),
            "affectedWorkspaces": sorted({row["workspace"] for row in rows}),
            "affectedFiles": sorted({row["path"] for row in rows}),
            "matchedPatterns": rows, "omittedPatterns": len(matching_owner) + len(command_matches) - len(rows),
            "applicableChanges": changes,
            "potentialConsumersOutsideDeclarationOwners": outside_owners,
            "availableCodemods": codemods,
            "confidence": "STATIC_ONLY" if rows else "UNKNOWN",
            "unknowns": unknowns,
            **({"suggestedChecks": [{"status": "NOT_RUN", "instruction":
                                    "Review the indicated npm scripts and compare the affected CLI behavior in the normal project checks."}]}
               if command_matches else {}),
        })
    result["evidenceMetrics"] = _transition_evidence(project, state, result["transitions"], integrities, registry, evidence_mode)
    return result

