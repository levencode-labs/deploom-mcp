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
import subprocess
import sys
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
    additions: dict[str, list[str]] = {}
    for edit in edits:
        section, name, version = edit["section"], edit["name"], edit["toVersion"]
        members = section_members[section]
        if not _VERSION.fullmatch(version):
            raise GoalContractError("WORKSPACE_PATCH_PREIMAGE_MISMATCH")
        if edit["fromSpec"] is None:
            if name in members:
                raise GoalContractError("WORKSPACE_PATCH_PREIMAGE_MISMATCH")
            additions.setdefault(section, []).append(f"{json.dumps(name)}: {json.dumps(version)}")
            continue
        start, end, before = members[name]
        if before != edit["fromSpec"]:
            raise GoalContractError("WORKSPACE_PATCH_PREIMAGE_MISMATCH")
        spans.append((start, end, json.dumps(version)))
    for section, values in additions.items():
        start, end, _ = sections[section]
        members = section_members[section]
        anchor = start + 1
        separator = ""
        if members:
            last_start, anchor, _ = next(reversed(members.values()))
            gap = source[anchor:end - 1]
            if "\n" in gap:
                line = source.rfind("\n", 0, last_start) + 1
                indent = re.match(r"[ \t]*", source[line:]).group()
                separator = ("\r\n" if "\r\n" in gap else "\n") + indent
            elif gap:
                separator = " "
        else:
            # Preserve whitespace around an originally empty object.
            gap = source[anchor:end - 1]
            separator = " " if gap and "\n" not in gap else ""
            if "\n" in gap:
                line = source.rfind("\n", 0, start) + 1
                indent = re.match(r"[ \t]*", source[line:]).group()
                separator = ("\r\n" if "\r\n" in gap else "\n") + indent + "  "
        prefix = "," if members else ""
        spans.append((anchor, anchor, prefix + separator + ("," + separator).join(values)))
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
        return b"".join(_manifest_patch(path, inputs[path], rows) for path, rows in sorted(grouped.items()))

    return _save_patch(project, observe_npm_workspace(project, plan_snapshot=render))


def _save_patch(project: Path, patch: bytes) -> dict:
    if not 0 < len(patch) <= _MAX_PATCH_BYTES:
        raise GoalContractError("WORKSPACE_PATCH_SIZE_UNSUPPORTED")
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


def _validate_edits_request(request: dict) -> None:
    if (request.get("schemaVersion") != 3 or request.get("goalKind") != "TARGETED_UPGRADE"
            or request.get("responseMode") != "EDITS" or "preflight" in request):
        raise GoalContractError("WORKSPACE_EDITS_REQUIRES_V3_TARGETED_PLAN")
    if type(request.get("migrationCard", False)) is not bool:
        raise GoalContractError("INVALID_WORKSPACE_MIGRATION_CARD_OPTION")
    if type(request.get("compactEdits", False)) is not bool:
        raise GoalContractError("INVALID_WORKSPACE_COMPACT_EDITS_OPTION")
    if "editsStrategy" in request and request["editsStrategy"] != "NATIVE_INTENT":
        raise GoalContractError("INVALID_WORKSPACE_EDITS_STRATEGY")


def _plan_native_intent(project: Path, request: dict) -> dict:
    from deploom_mcp.distribution import resolve_core_dir
    from deploom_mcp.workspace_native_intent import prepare_native_intent

    try:
        core_dir = str(resolve_core_dir())
        sys.path.insert(0, core_dir)
        try:
            from resolution_inventory import npm_lock_resolution_slots
            import workspace_npm_lock_delta
        finally:
            sys.path.remove(core_dir)
        return prepare_native_intent(
            project, request, npm_lock_resolution_slots, render=_manifest_patch, save=_save_patch,
            root_peer_checker=getattr(workspace_npm_lock_delta, "forced_root_peer_conflicts", None),
        )
    except GoalContractError as exc:
        return {"ok": False, "error": {"code": exc.code}}
    except ImportError:
        return {"ok": False, "error": {"code": "WORKSPACE_PLANNING_CORE_UNAVAILABLE"}}
    except (OSError, KeyError, ValueError):
        return {"ok": False, "error": {"code": "WORKSPACE_PATCH_UNAVAILABLE"}}


async def plan_workspace_edits(project_path: str, request: dict) -> dict:
    """Use the existing planner and replace verbose evidence with a local patch."""
    try:
        _validate_edits_request(request)
    except GoalContractError as exc:
        return {"ok": False, "error": {"code": exc.code}}
    if "editsStrategy" in request:
        return await asyncio.to_thread(_plan_native_intent, Path(project_path), request)

    from deploom_mcp.workspace_upgrade import get_workspace_upgrade_plan

    include_card = request.get("migrationCard", False)
    compact = request.get("compactEdits", False)
    plan = await get_workspace_upgrade_plan(
        project_path, {key: value for key, value in request.items()
                       if key not in {"responseMode", "migrationCard", "compactEdits"}},
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
    response = {
        **{key: plan[key] for key in ("ok", "goalKind", "inventoryDigest", "requestId", "candidateId", "authority",
                                     "verification", "executionReady", "details", "packageManagerValidation",
                                     "installation", "checks", "runtime", "acceptance", "policy")},
        "kind": "npm-workspace-manifest-edits", "schemaVersion": 1,
        "constraintModel": model, "heldCount": plan["coverage"]["heldDeclarationCount"],
        "changeCount": len(edits), "patch": patch,
        "action": action,
    }
    if compact and patch:
        response = {key: response[key] for key in (
            "ok", "kind", "schemaVersion", "inventoryDigest", "requestId", "candidateId",
            "authority", "verification", "executionReady", "changeCount", "patch", "action",
        )}
        response["constraintModel"] = {"status": model["status"]}
        response["compactEdits"] = True
    if include_card:
        from deploom_mcp.workspace_migration_card import build_migration_card

        response["migrationCard"] = await asyncio.to_thread(
            build_migration_card, Path(project_path), plan["jointAssignment"]["targets"], model["status"],
        )
    return response


def _checked_patch_bytes(project: Path, inventory_digest: str, patch: Path, sha256: str,
                         *, inventory: dict | None = None) -> bytes:
    if inventory is None:
        inventory = observe_npm_workspace(project, allow_root_only=True)
    if inventory["inventoryDigest"] != inventory_digest:
        raise GoalContractError("STALE_WORKSPACE_INVENTORY")
    with patch.open("rb") as stream:
        data = stream.read(_MAX_PATCH_BYTES + 1)
    if not 0 < len(data) <= _MAX_PATCH_BYTES or hashlib.sha256(data).hexdigest() != sha256:
        raise GoalContractError("WORKSPACE_PATCH_DIGEST_MISMATCH")
    return data


def check_workspace_patch(project: Path, inventory_digest: str, patch: Path, sha256: str) -> None:
    _checked_patch_bytes(project, inventory_digest, patch, sha256)


def _stage_manifest_patch(project: Path, data: bytes, owners: list[dict]) -> list[tuple[Path, bytes, bytes]]:
    # A private tree keeps Git attributes and filters from changing manifest bytes.
    manifests = {owner["manifestPath"]: owner["manifestSha256"] for owner in owners}
    with tempfile.TemporaryDirectory() as directory:
        staging = Path(directory).resolve()
        attributes = staging / "empty-attributes"
        attributes.touch()
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        env.update(GIT_CEILING_DIRECTORIES=str(staging.parent), GIT_ATTR_NOSYSTEM="1",
                   GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        command = ["git", "-c", "core.autocrlf=false", "-c", f"core.attributesFile={attributes}",
                   "-C", str(staging), "apply", "--unidiff-zero", "--whitespace=nowarn"]

        def apply(options, error):
            result = subprocess.run([*command, *options, "-"], input=data, capture_output=True, env=env)
            if result.returncode:
                raise GoalContractError(error)
            return result.stdout

        numstat = apply(["--numstat", "-z"], "WORKSPACE_PATCH_NOT_APPLICABLE")
        paths = set()
        for entry in numstat.rstrip(b"\0").split(b"\0"):
            parts = entry.split(b"\t", 2)
            if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
                raise GoalContractError("WORKSPACE_PATCH_PATH_UNSUPPORTED")
            name = parts[2].decode("ascii", errors="replace")
            if name not in manifests or name in paths:
                raise GoalContractError("WORKSPACE_PATCH_PATH_UNSUPPORTED")
            paths.add(name)

        originals = {}
        for name in sorted(paths):
            path = project / name
            if path.resolve(strict=True) != path:
                raise GoalContractError("WORKSPACE_PATCH_PATH_UNSUPPORTED")
            before = path.read_bytes()
            if hashlib.sha256(before).hexdigest() != manifests[name]:
                raise GoalContractError("STALE_WORKSPACE_INVENTORY")
            staged = staging / name
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(before)
            originals[name] = before
        apply(["--check"], "WORKSPACE_PATCH_NOT_APPLICABLE")
        apply([], "WORKSPACE_PATCH_APPLY_FAILED")
        updates = []
        for name, before in originals.items():
            staged = staging / name
            if not staged.is_file() or staged.resolve(strict=True) != staged:
                raise GoalContractError("WORKSPACE_PATCH_PATH_UNSUPPORTED")
            updates.append((project / name, before, staged.read_bytes()))
        return updates


def _replace_manifest(path: Path, raw: bytes) -> None:
    descriptor, filename = tempfile.mkstemp(dir=path.parent, prefix=".deploom-manifest-")
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
        os.chmod(temporary, path.stat().st_mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            os.chmod(temporary, 0o600)
            temporary.unlink()


def apply_workspace_patch(project: Path, inventory_digest: str, patch: Path, sha256: str) -> None:
    """Apply a snapshot-bound manifest patch while preserving the working-tree bytes."""
    project = project.resolve(strict=True)
    inventory = observe_npm_workspace(project, allow_root_only=True)
    data = _checked_patch_bytes(project, inventory_digest, patch, sha256, inventory=inventory)
    updates = _stage_manifest_patch(project, data, inventory["owners"])
    if observe_npm_workspace(project, allow_root_only=True)["inventoryDigest"] != inventory_digest:
        raise GoalContractError("STALE_WORKSPACE_INVENTORY")
    committed = []
    try:
        for path, before, after in updates:
            if path.resolve(strict=True) != path or path.read_bytes() != before:
                raise GoalContractError("STALE_WORKSPACE_INVENTORY")
            _replace_manifest(path, after)
            committed.append((path, before, after))
    except (OSError, GoalContractError):
        rollback_failed = False
        for path, before, after in reversed(committed):
            try:
                if path.resolve(strict=True) != path or path.read_bytes() != after:
                    rollback_failed = True
                    continue
                _replace_manifest(path, before)
            except OSError:
                rollback_failed = True
        if rollback_failed:
            raise GoalContractError("WORKSPACE_PATCH_ROLLBACK_FAILED")
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    plan = commands.add_parser("plan", help="plan with the bundled Core and export a manifest patch")
    plan.add_argument("project", type=Path)
    plan.add_argument("request", type=Path, help="workspace request JSON with responseMode EDITS")
    for operation, help_text in (
        ("check", "check project inventory and patch digest before git apply"),
        ("apply", "validate and apply the manifest patch to the local working tree"),
    ):
        patch_command = commands.add_parser(operation, help=help_text)
        patch_command.add_argument("project", type=Path)
        patch_command.add_argument("inventory_digest")
        patch_command.add_argument("patch", type=Path)
        patch_command.add_argument("sha256")
    args = parser.parse_args()
    project = Path(os.path.abspath(args.project))
    try:
        if args.operation == "plan":
            with args.request.open(encoding="utf-8-sig") as stream:
                request = json.load(stream)
            if not isinstance(request, dict):
                raise GoalContractError("INVALID_WORKSPACE_GOAL_REQUEST")
            if request.get("editsStrategy") == "NATIVE_INTENT":
                # This route only reads local files. Windows event loops require a loopback socket.
                _validate_edits_request(request)
                result = _plan_native_intent(project, request)
            else:
                result = asyncio.run(plan_workspace_edits(str(project), request))
        elif args.operation == "apply":
            apply_workspace_patch(project, args.inventory_digest, args.patch, args.sha256)
            result = {"ok": True, "status": "MANIFEST_PATCH_APPLIED", "acceptance": "NOT_RUN"}
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
