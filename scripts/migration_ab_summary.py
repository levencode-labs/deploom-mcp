"""Aggregate every planned pair, including missing arms and failed oracle checks."""
from collections import Counter
import json
from pathlib import Path
import sys

from migration_ab_preflight import schedule, write_json


def report(directory):
    rows, pairs, hashes = [], [], set()
    for pair in schedule()["plannedPairs"]:
        path = directory / ("pair-" + pair["id"])
        observed = {}
        for side in pair["order"]:
            file = path / f"{side}.json"
            if file.exists():
                row = json.loads(file.read_bytes())
                if row["pair"] != pair["id"] or row["arm"] != side:
                    raise ValueError("result identity mismatch")
            else:
                row = {"pair": pair["id"], "arm": side, "acceptedWithinBudget": False,
                       "terminal": "MISSING", "verification": "NOT_RUN", "requests": 0}
            observed[side] = row
            rows.append(row)
        receipt = path / "pair.json"
        if receipt.exists():
            hashes.add(json.loads(receipt.read_bytes())["corpusSha256"])
        pairs.append({"id": pair["id"], "task": pair["task"], "cache": pair["cache"],
                      "order": pair["order"], "A": observed["A"]["acceptedWithinBudget"],
                      "B": observed["B"]["acceptedWithinBudget"],
                      "pairedBMinusASeconds": round(observed["B"]["agentSeconds"] -
                                                    observed["A"]["agentSeconds"], 3)
                      if all(r["acceptedWithinBudget"] for r in observed.values()) else None})
    counts = {}
    for side in ("A", "B"):
        group = [r for r in rows if r["arm"] == side]
        counts[side] = {"scheduled": len(group), "accepted": sum(r["acceptedWithinBudget"] for r in group),
                        "terminal": dict(Counter(r["terminal"] for r in group)),
                        "apiRequests": sum(r["requests"] for r in group),
                        "inputTokens": sum(r.get("inputTokens", 0) for r in group),
                        "outputTokens": sum(r.get("outputTokens", 0) for r in group)}
    missing = sum(r["terminal"] == "MISSING" for r in rows)
    completed = len(hashes) == 1 and not missing and all(r["requests"] for r in rows)
    return {"status": "COMPLETE" if completed else "INCOMPLETE", "corpusSha256": sorted(hashes),
            "armCounts": counts, "missing": missing, "pairs": pairs, "runs": rows}


if __name__ == "__main__":
    source, output = map(Path, sys.argv[1:])
    result = report(source)
    write_json(output, result)
    print(json.dumps({key: result[key] for key in ("status", "armCounts", "missing")}))
    sys.exit(0 if result["status"] == "COMPLETE" else 1)
