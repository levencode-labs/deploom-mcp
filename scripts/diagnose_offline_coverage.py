"""Read a saved flat Draft result and compare its current direct pairs locally."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from semantic_version import Version

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from deploom_mcp.security_projection import project_current_security


def historical_database(directory, result):
    # sha256 identifies the gzip release. databaseSha256 identifies the installed SQLite bytes.
    snapshot = result["planningOptions"]["securitySnapshot"]
    manifest = json.loads((directory / "active.json").read_bytes())
    digest = manifest["sha256"]
    if (snapshot.get("status") != "ready" or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or snapshot.get("sha256") != digest or manifest.get("ecosystem") != "npm"
            or manifest.get("schema") != 1):
        raise ValueError("Snapshot edition differs from the ready edition recorded by this run")
    path = directory.resolve() / (digest + ".sqlite")
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != manifest.get("databaseSha256"):
            raise ValueError("Unpacked snapshot digest mismatch or missing")
    return path


def row_key(row):
    return tuple(row.get(key) for key in ("project", "kind", "package", "current", "requestedSpec"))


def exact_pair(name, version):
    if not isinstance(name, str) or not name or not isinstance(version, str):
        return None
    try:
        return (name, version) if str(Version(version)) == version else None
    except ValueError:
        return None


def diagnose(result_path, *, snapshot_dir=None, inventory_path=None, focus=None):
    result = json.loads(result_path.read_bytes())
    data = result_path.with_name("plan.json").read_bytes()
    if hashlib.sha256(data).hexdigest() != result["hashes"]["plan"]:
        raise ValueError("plan.json does not match the saved result")
    plan = json.loads(data)
    emitted_unknowns = {
        row_key(item) for item in plan["unknowns"]
        if "OSV/security state unknown for this package in this Draft run" in item.get("reason", "")
    }
    rows = [row for project in plan["proposals"] for row in project["rows"] if row["status"] != "excluded"]
    marked = Counter()
    recorded = Counter()
    replayed = Counter()
    direct_pairs = set()
    focused = []
    db = historical_database(snapshot_dir, result) if snapshot_dir is not None else None
    if db is not None:
        sys.path.insert(0, str(ROOT / "upstream" / "deploom"))
        from osv_offline import assess_database
    for row in rows:
        name, version = row["package"], row["current"]
        pair = exact_pair(name, version)
        # Early exits have no resolved registry subject even when their text looks like semver.
        non_registry = row.get("latest") == "internal/non-registry"
        if pair and not non_registry:
            direct_pairs.add(pair)
        tag = row.get("draftUnknown")
        evidence = project_current_security(row.get("currentSecurityEvidence"))
        if evidence is not None:
            if evidence["status"] != "UNKNOWN":
                continue
            reason = evidence["reason"]
        elif row_key(row) in emitted_unknowns or tag in ("osv-unavailable", "registry-failed"):
            if non_registry:
                reason = "NON_REGISTRY_SPEC"
            elif tag == "registry-failed":
                reason = "REGISTRY_METADATA_UNAVAILABLE"
            elif row.get("scanState") in ("pending", "attempted"):
                reason = "QUERY_NOT_COMPLETED"
            else:
                reason = "LEGACY_REASON_NOT_RECORDED"
        else:
            continue
        marker = tag if tag in ("osv-unavailable", "registry-failed") else "no-specific-marker"
        marked[marker] += 1
        recorded[reason] += 1
        entry = {"recordedReason": reason, "marker": marker}
        if db is not None and reason == "LEGACY_REASON_NOT_RECORDED":
            assessment = assess_database(db, name, [version], os.environ.get("DEPLOOM_MCP_PRIVATE_SCOPES", ""),
                                         os.environ.get("DEPLOOM_MCP_PUBLIC_NAMES", ""))[version]
            replay_reason = assessment.reason or ("ASSESSED_FINDINGS" if assessment.findings else "ASSESSED_ZERO")
            replayed[replay_reason] += 1
            entry["offlineReplay"] = replay_reason
        if name == focus:
            focused.append(entry)
    metadata = result["metadata"]
    report = {"runId": result["runId"], "scope": "current direct declarations",
              "reported": {k: metadata.get(k) for k in ("securityTotal", "securityKnown", "processed", "processedTotal")},
              "identifiedUnknownRows": sum(recorded.values()), "recordedReasons": dict(recorded),
              "recordedMarkers": dict(marked)}
    total, known = metadata.get("securityTotal"), metadata.get("securityKnown")
    if type(total) is int and type(known) is int:
        report["accountsForReportedUnknowns"] = total - known == sum(recorded.values())
    if snapshot_dir is not None:
        report["replayOfLegacyUnknownsWithSuppliedPolicy"] = dict(replayed)
    if focus is not None:
        report["focusUnknownRows"] = focused
    if inventory_path is not None:
        inventory = json.loads(inventory_path.read_bytes())
        pairs = set()
        for item in inventory["pairs"]:
            pair = exact_pair(*item) if isinstance(item, list) and len(item) == 2 else None
            if pair is None:
                raise ValueError("Invalid exact pair in canonical inventory")
            pairs.add(pair)
        report["pairIntersection"] = {"distinctDirectPairs": len(direct_pairs), "inventoryPairs": len(pairs),
                                      "sharedPairs": len(direct_pairs & pairs),
                                      "directPairsMissingFromInventory": len(direct_pairs - pairs),
                                      "inventoryComplete": inventory.get("complete") is True}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path, help="Original draft/result.json beside its plan.json")
    parser.add_argument("--snapshot-dir", type=Path, help="Replay using the same edition and the supplied MCP scope/name policy")
    parser.add_argument("--inventory", type=Path, help="canonical-yarn-inventory.json from a saved audit workspace")
    parser.add_argument("--focus", help="Print the selected package's reasons without disclosing other names")
    args = parser.parse_args()
    try:
        print(json.dumps(diagnose(args.result, snapshot_dir=args.snapshot_dir,
                                  inventory_path=args.inventory, focus=args.focus), indent=2))
    except (OSError, KeyError, ValueError, TypeError) as exc:
        parser.exit(2, f"Cannot diagnose saved run: {exc}\n")


if __name__ == "__main__":
    main()
