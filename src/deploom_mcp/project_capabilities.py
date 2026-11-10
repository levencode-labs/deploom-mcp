"""Local route discovery: no network, package manager, execution grant or writes."""
from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path

from deploom_mcp.audit_evidence import AuditError
from deploom_mcp.audit_worker import detect_manager
from deploom_mcp.goal_contract import GoalContractError, canonical_digest
from deploom_mcp.workspace_input_io import selected_root
from deploom_mcp.workspace_inventory import _read, _read_bytes, _safe, observe_npm_workspace

_INPUTS = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
           ".npmrc", ".yarnrc", ".yarnrc.yml")


def _inputs(project, root_handle):
    raw, manifest = _read(project / "package.json", 2 * 1024 * 1024, root=project, root_handle=root_handle)
    inputs = {"package.json": raw}
    for name in _INPUTS:
        try:
            (project / name).lstat()
        except FileNotFoundError:
            continue
        inputs[name] = _read_bytes(project / name, 32 * 1024 * 1024 if name.endswith(".json")
                                  or name.endswith(".lock") else 64 * 1024, root=project, root_handle=root_handle)
    return inputs, manifest


def _route(operation, tool, *, support="SUPPORTED", settings=(), preconditions=()):
    missing = [name for name in settings if not os.environ.get(name)]
    return {"operation": operation, "tool": tool, "support": support,
            "hostConfiguration": "NOT_REQUIRED" if not settings else "MISSING" if missing else "PRESENT_NOT_VALIDATED",
            "authorization": "NOT_REQUIRED" if operation == "PLAN" and not settings else "NOT_GRANTED_BY_DISCOVERY",
            "missingPreconditions": [*missing, *preconditions]}


def get_project_capabilities(project_path: str) -> dict:
    """Inspect one explicit root. Configuration presence never grants execution."""
    try:
        if not isinstance(project_path, str) or not Path(project_path).is_absolute() or ".." in Path(project_path).parts:
            raise GoalContractError("INVALID_PROJECT_PATH")
        project = Path(project_path)
        original = _safe(project, directory=True)
        for ancestor in project.parents:
            _safe(ancestor, directory=True)
        with selected_root(project, original) as root_handle:
            inputs, manifest = _inputs(project, root_handle)
            manager = detect_manager(inputs, "auto")
            has_workspaces = "workspaces" in manifest
            inventory = None
            if manager == "npm" and has_workspaces:
                inventory = observe_npm_workspace(project)
            lock_name = next(name for name in ("npm-shrinkwrap.json", "package-lock.json", "yarn.lock") if name in inputs)
            lock_version = None
            if manager == "npm":
                _, lock = _read(project / lock_name, 32 * 1024 * 1024, root=project, root_handle=root_handle)
                if type(lock.get("lockfileVersion")) is not int:
                    raise GoalContractError("INVALID_LOCK_VERSION")
                lock_version = lock["lockfileVersion"]
            if _inputs(project, root_handle)[0] != inputs:
                raise GoalContractError("WORKSPACE_INPUT_CHANGED")
        workspace_kind = "NPM_WORKSPACES" if inventory is not None else "OTHER_WORKSPACES" if has_workspaces else "STANDALONE"
        routes = []
        if inventory is not None:
            routes = [
                _route("PLAN", "get_upgrade_plan", settings=("DEPLOOM_WORKSPACE_POLICY_REGISTRY",)),
                _route("PREFLIGHT", "get_upgrade_plan", settings=("DEPLOOM_WORKSPACE_NPM_ARTIFACTS",
                       "DEPLOOM_WORKSPACE_NPM_CACHE", "DEPLOOM_WORKSPACE_NPM_REGISTRY"),
                       preconditions=("EXACT_BOUND_CANDIDATE",)),
                _route("VERIFY", "workspace_acceptance_cli", support="HOST_ADAPTER_ONLY",
                       preconditions=("HOST_CHECK_PROFILE", "BOUND_RESOLVER_RECEIPT", "EXPLICIT_HOST_AUTHORIZATION")),
            ]
            goals = ["TARGETED_UPGRADE", "MONOREPO_TO_POLICY"]
            next_action = "USE_WORKSPACE_REQUEST_V3"
        else:
            routes = [_route("PLAN", "get_upgrade_plan", support="CONDITIONAL" if has_workspaces else "SUPPORTED",
                             preconditions=("FLAT_ROUTE_WORKSPACE_COVERAGE_REQUIRES_REVIEW",) if has_workspaces else ())]
            goals = ["DEPENDENCY_HEALTH_PLAN"]
            next_action = "USE_FLAT_UPGRADE_REQUEST"
            if manager == "npm" and not has_workspaces and lock_version in (2, 3):
                routes.append(_route("GOAL_PLAN", "get_goal_plan", support="CONDITIONAL",
                                     preconditions=("SUPPORTED_PYTHON_TOOLCHAIN", "EXPLICIT_GOAL")))
                routes.append(_route("VERIFY", "verify_goal_candidate", support="CONDITIONAL",
                    settings=("DEPLOOM_MCP_EXECUTION_MANIFEST", "DEPLOOM_MCP_EXECUTION_MANIFEST_SHA256"),
                    preconditions=("SUPPORTED_WINDOWS_NTFS_PROFILE", "REGISTERED_CANDIDATE", "CURRENT_BINDING",
                                   "EXPLICIT_HOST_AUTHORIZATION")))
                goals.append("EXACT_FUNCTIONAL_MAINTENANCE")
        return {"ok": True, "schemaVersion": 1, "kind": "project-capabilities",
                "authority": "DISCOVERY_ONLY", "executionReady": False, "verification": "NOT_VERIFIED",
                "manager": manager, "lockKind": lock_name, "lockVersion": lock_version,
                "workspaceKind": workspace_kind,
                "inventoryDigest": inventory["inventoryDigest"] if inventory is not None else None,
                "inputDigest": canonical_digest({name: sha256(raw).hexdigest() for name, raw in inputs.items()}),
                "sourceContinuity": "READ_TWICE_ONLY_NOT_AN_EXECUTION_BINDING",
                "routes": routes, "supportedGoalKinds": goals, "nextAction": next_action}
    except (GoalContractError, AuditError) as exc:
        return {"ok": False, "error": {"code": exc.code}, "authority": "DISCOVERY_ONLY",
                "executionReady": False, "verification": "NOT_VERIFIED"}
    except (OSError, ValueError, TypeError, StopIteration):
        return {"ok": False, "error": {"code": "CAPABILITY_INPUT_UNAVAILABLE"}, "authority": "DISCOVERY_ONLY",
                "executionReady": False, "verification": "NOT_VERIFIED"}
