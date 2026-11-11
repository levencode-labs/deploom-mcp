"""Local, snapshot-bound manifest patches for workspace upgrade plans."""
from __future__ import annotations

import argparse
import asyncio
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from deploom_mcp.blueprint import resolve_output_outside
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.goal_execution_registry import configured_state_root
from deploom_mcp.workspace_inventory import observe_npm_workspace

_MAX_PATCH_BYTES = 1024 * 1024
_SAFE_PATH = re.compile(r"[a-zA-Z0-9_./@ -]+")
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def _object_members(text: str, start: int = 0) -> dict:
    """Locate value spans in an already validated JSON object's text."""
    decoder = json.JSONDecoder()
    spaces = re.compile(r"[ \t\r\n]*")
    index = spaces.match(text, start).end() + 1
    result = {}
    while True:
        index = spaces.match(text, index).end()
        if text[index] == "}":
            return result
        name, end = decoder.raw_decode(text, index)
        begin = spaces.match(text, spaces.match(text, end).end() + 1).end()
        value, end = decoder.raw_decode(text, begin)
        result[name] = (begin, end, value)
        index = spaces.match(text, end).end()
        if text[index] == "}":
            return result
        index += 1


def _manifest_patch(path: str, raw: bytes, edits: list[dict]) -> bytes:
    if not _SAFE_PATH.fullmatch(path):
        raise GoalContractError("WORKSPACE_PATCH_PATH_UNSUPPORTED")
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    source = raw.decode("utf-8-sig")
    sections = _object_members(source)
    section_members = {name: _object_members(source, sections[name][0])
                       for name in {edit["section"] for edit in edits}}
    spans = []
    for edit in edits:
        start, end, before = section_members[edit["section"]][edit["name"]]
        if before != edit["fromSpec"] or not _VERSION.fullmatch(edit["toVersion"]):
            raise GoalContractError("WORKSPACE_PATCH_PREIMAGE_MISMATCH")
        spans.append((start, end, json.dumps(edit["toVersion"])))
    updated = source
    for start, end, replacement in sorted(spans, reverse=True):
        updated = updated[:start] + replacement + updated[end:]
    after = bom + updated.encode("utf-8")

    def lines(data):
        # LF and CRLF are both supported. A trailing empty split is not a line.
        rows = data.split(b"\n")
        return [row + b"\n" for row in rows[:-1]] + ([rows[-1]] if rows[-1] else [])

    # Context-free hunks keep unrelated manifest lines out of the patch.
    diff = difflib.diff_bytes(
        difflib.unified_diff, lines(raw), lines(after),
        fromfile=f"a/{path}\t".encode("ascii"), tofile=f"b/{path}\t".encode("ascii"), n=0,
    )
    return b"".join(row if row.endswith(b"\n") else row + b"\n\\ No newline at end of file\n" for row in diff)


def _write_patch(project: Path, inventory_digest: str, edits: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = {}
    for edit in edits:
        owner = edit["owner"]
        path = "package.json" if owner == "." else f"{owner}/package.json"
        grouped.setdefault(path, []).append(edit)

    def render(digest, declarations, packages, inputs):
        if digest != inventory_digest:
            raise GoalContractError("STALE_WORKSPACE_INVENTORY")
        patch = b"".join(_manifest_patch(path, inputs[path], rows) for path, rows in sorted(grouped.items()))
        if not 0 < len(patch) <= _MAX_PATCH_BYTES:
            raise GoalContractError("WORKSPACE_PATCH_SIZE_UNSUPPORTED")
        return patch

    patch = observe_npm_workspace(project, plan_snapshot=render)
    directory = resolve_output_outside(project, configured_state_root())
    if directory is None:
        raise GoalContractError("WORKSPACE_PATCH_OUTPUT_OVERLAPS_PROJECT")
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="wb", prefix="workspace-edits-", suffix=".patch",
                                     dir=directory, delete=False) as stream:
        try:
            stream.write(patch)
            stream.flush()
        except OSError:
            stream.close()
            Path(stream.name).unlink(missing_ok=True)
            raise
    return {"path": stream.name, "sha256": hashlib.sha256(patch).hexdigest(), "format": "GIT_UNIDIFF_ZERO"}


async def plan_workspace_edits(project_path: str, request: dict) -> dict:
    """Use the existing planner and replace verbose evidence with a local patch."""
    from deploom_mcp.workspace_upgrade import get_workspace_upgrade_plan

    if (request.get("schemaVersion") != 3 or request.get("goalKind") != "TARGETED_UPGRADE"
            or request.get("responseMode") != "EDITS" or "preflight" in request):
        return {"ok": False, "error": {"code": "WORKSPACE_EDITS_REQUIRES_V3_TARGETED_PLAN"}}
    plan = await get_workspace_upgrade_plan(
        project_path, {key: value for key, value in request.items() if key != "responseMode"},
    )
    if not plan["ok"]:
        return plan
    model = plan["constraintModel"]
    edits = []
    if model["status"] == "SAT_IN_MODELED_DOMAIN":
        pinned = {tuple(row["selector"][key] for key in ("owner", "section", "name"))
                  for row in plan["intent"]["targets"] if row["exactTarget"] is not None}
        for row in plan["jointAssignment"]["targets"]:
            owner, section, name = row["declaration"]
            version = row["selectedVersion"]
            if not isinstance(version, str) or not _VERSION.fullmatch(version):
                return {"ok": False, "error": {"code": "WORKSPACE_PATCH_ASSIGNMENT_INVALID"}}
            baseline = row["baselineLock"]
            if (owner, section, name) not in pinned and baseline and version == baseline[1]:
                continue
            if version != row["declaredSpec"]:
                edits.append({"owner": owner, "section": section, "name": name,
                              "fromSpec": row["declaredSpec"], "toVersion": version})
    patch = None
    if edits:
        try:
            patch = await asyncio.to_thread(_write_patch, Path(project_path), plan["inventoryDigest"], edits)
        except GoalContractError as exc:
            return {"ok": False, "error": {"code": exc.code}}
        except (OSError, KeyError, ValueError):
            return {"ok": False, "error": {"code": "WORKSPACE_PATCH_UNAVAILABLE"}}
    action = plan["action"]
    if patch:
        action = {"kind": "REVIEW_MANIFEST_PATCH"}
    elif model["status"] == "SAT_IN_MODELED_DOMAIN":
        action = {"kind": "NO_MANIFEST_CHANGES"}
    return {
        **{key: plan[key] for key in ("ok", "goalKind", "inventoryDigest", "requestId", "candidateId", "authority",
                                     "verification", "executionReady", "details", "packageManagerValidation",
                                     "installation", "checks", "runtime", "acceptance", "policy")},
        "kind": "npm-workspace-manifest-edits", "schemaVersion": 1,
        "constraintModel": model, "heldCount": plan["coverage"]["heldDeclarationCount"],
        "changeCount": len(edits), "patch": patch,
        "action": action,
    }


def check_workspace_patch(project: Path, inventory_digest: str, patch: Path, sha256: str) -> None:
    if observe_npm_workspace(project)["inventoryDigest"] != inventory_digest:
        raise GoalContractError("STALE_WORKSPACE_INVENTORY")
    with patch.open("rb") as stream:
        data = stream.read(_MAX_PATCH_BYTES + 1)
    if not 0 < len(data) <= _MAX_PATCH_BYTES or hashlib.sha256(data).hexdigest() != sha256:
        raise GoalContractError("WORKSPACE_PATCH_DIGEST_MISMATCH")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    plan = commands.add_parser("plan", help="plan with the bundled Core and export a manifest patch")
    plan.add_argument("project", type=Path)
    plan.add_argument("request", type=Path, help="workspace request JSON with responseMode EDITS")
    check = commands.add_parser("check", help="check project inventory and patch digest before git apply")
    check.add_argument("project", type=Path)
    check.add_argument("inventory_digest")
    check.add_argument("patch", type=Path)
    check.add_argument("sha256")
    args = parser.parse_args()
    project = Path(os.path.abspath(args.project))
    try:
        if args.operation == "plan":
            with args.request.open(encoding="utf-8-sig") as stream:
                request = json.load(stream)
            if not isinstance(request, dict):
                raise GoalContractError("INVALID_WORKSPACE_GOAL_REQUEST")
            result = asyncio.run(plan_workspace_edits(str(project), request))
        else:
            check_workspace_patch(project, args.inventory_digest, args.patch, args.sha256)
            result = {"ok": True}
    except GoalContractError as exc:
        result = {"ok": False, "error": {"code": exc.code}}
    except (OSError, ValueError):
        result = {"ok": False, "error": {"code": "WORKSPACE_EDITS_INPUT_UNAVAILABLE"}}
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
