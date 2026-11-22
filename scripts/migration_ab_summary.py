"""Aggregate every planned pair, including missing arms and failed oracle checks."""
from collections import Counter
import json
from pathlib import Path
import sys

from migration_ab_preflight import schedule, write_json


def report(directory):
    rows, pairs, hashes = [], [], set()
    receipts = 0
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
            record = json.loads(receipt.read_bytes())
            if any(record[k] != pair[k] for k in ("id", "task", "cache", "order")):
                raise ValueError("pair receipt identity mismatch")
            hashes.add(record["corpusSha256"])
            receipts += 1
        pairs.append({"id": pair["id"], "task": pair["task"], "cache": pair["cache"],
                      "order": pair["order"], "A": observed["A"]["acceptedWithinBudget"],
                      "B": observed["B"]["acceptedWithinBudget"],
                      "outcomes": {k: {"terminal": v["terminal"], "verification": v["verification"]}
                                   for k, v in observed.items()},
                      "pairedBMinusASeconds": round(observed["B"]["agentSeconds"] -
                                                    observed["A"]["agentSeconds"], 3)
                      if all(r["acceptedWithinBudget"] for r in observed.values()) else None})
    counts = {}
    for side in ("A", "B"):
        group = [r for r in rows if r["arm"] == side]
        counts[side] = {"scheduled": len(group), "accepted": sum(r["acceptedWithinBudget"] for r in group),
                        "terminal": dict(Counter(r["terminal"] for r in group)),
                        "apiRequests": sum(r["requests"] for r in group),
                        "responses": sum(r.get("responses", 0) for r in group),
                        "sessionsUsingMcp": sum(any(t["name"] == "deploom_plan" for t in r.get("toolCalls", []))
                                                for r in group),
                        "sessionsReadingSkill": sum(any(t["name"] == "read_skill" for t in r.get("toolCalls", []))
                                                    for r in group),
                        "inputTokens": sum(r.get("inputTokens", 0) for r in group),
                        "outputTokens": sum(r.get("outputTokens", 0) for r in group)}
    missing = sum(r["terminal"] == "MISSING" for r in rows)
    completed = len(hashes) == 1 and receipts == len(pairs) and not missing and all(
        r.get("responses", 0) for r in rows)
    return {"status": "COMPLETE" if completed else "INCOMPLETE", "corpusSha256": sorted(hashes),
            "armCounts": counts, "missing": missing, "pairs": pairs, "runs": rows}


if __name__ == "__main__":
    source, output = map(Path, sys.argv[1:])
    result = report(source)
    write_json(output, result)
    print(json.dumps({key: result[key] for key in ("status", "armCounts", "missing", "corpusSha256", "pairs")}))
    sys.exit(0 if result["status"] == "COMPLETE" else 1)
