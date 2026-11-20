"""Validated, advisory-only observations and recipes for public migration examples."""
import json
import re
import sqlite3
from contextlib import closing

from .public_cache import get

BASE = "https://levencode-labs.github.io/deploom-npm/v1/"
PACKAGES = {"react", "react-dom", "react-test-renderer", "vite", "typescript", "eslint", "semver"}
CHECKS = {"install", "build", "test", "lint", "typecheck", "peer-compatibility"}
RECIPES = {
    "align-react-renderers": "Check the target renderer peer ranges, then run the recipient's renderer tests.",
    "peer-companion": "Read the target package peer range and run npm ls on the recipient's installed tree.",
    "eslint-flat-config": "Check configuration format and ignore patterns, then run the recipient's lint task.",
}
VERSION = re.compile(r"(?:0|[1-9][0-9]{0,7})(?:\.(?:0|[1-9][0-9]{0,7})){2}")
SOURCE = re.compile(r"https://github\.com/levencode-labs/deploom-npm/blob/[a-f0-9]{40}/fixtures/[a-z0-9_-]+/check\.cjs")


def validate(kind, rows):
    if kind not in {"history", "knowledge"} or not isinstance(rows, list) or len(rows) > 2000:
        raise ValueError("Invalid shared record collection")
    payload_keys = {"check", "outcome"} if kind == "history" else {"recipe"}
    keys = {"schema", "package", "from", "to", "source"} | payload_keys
    seen = {}
    for row in rows:
        if (not isinstance(row, dict) or set(row) != keys or type(row["schema"]) is not int or row["schema"] != 1 or
                not all(isinstance(row[k], str) for k in keys - {"schema"}) or row["package"] not in PACKAGES or
                not VERSION.fullmatch(row["from"]) or not VERSION.fullmatch(row["to"]) or row["from"] == row["to"] or
                not SOURCE.fullmatch(row["source"])):
            raise ValueError("Unsupported record, public package, or evidence URL")
        if kind == "history" and (row["check"] not in CHECKS or row["outcome"] not in {"passed", "failed"}):
            raise ValueError("Invalid observation")
        if kind == "knowledge" and row["recipe"] not in RECIPES:
            raise ValueError("Unknown recipe")
        identity = (row["package"], row["from"], row["to"], row["source"], row.get("check", row.get("recipe")))
        if identity in seen and seen[identity] != row:
            raise ValueError("Conflicting results for one example")
        seen[identity] = row
    return list(seen.values())


def refresh(root, fetch=get):
    root.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(root / "records.sqlite", timeout=60)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS snapshots (kind TEXT PRIMARY KEY, etag TEXT, data TEXT NOT NULL)")
        for kind in ("history", "knowledge"):
            old = db.execute("SELECT etag FROM snapshots WHERE kind=?", (kind,)).fetchone()
            raw, etag = fetch(BASE + kind + ".json", 2_000_000, old[0] if old else None)
            if raw is None:
                if not old:
                    raise ValueError("Missing conditional record snapshot")
                continue
            rows = validate(kind, json.loads(raw))
            db.execute("INSERT OR REPLACE INTO snapshots VALUES(?,?,?)", (kind, etag, json.dumps(rows)))


def lookup(root, package, from_version, to_version):
    result = {"authority": "ADVISORY_ONLY", "status": "no-matching-record", "observations": [], "recipes": []}
    if root is None or not (root / "records.sqlite").is_file():
        result["status"] = "unavailable"
        return result
    with closing(sqlite3.connect((root / "records.sqlite").resolve().as_uri() + "?mode=ro", uri=True)) as db:
        for kind, data in db.execute("SELECT kind,data FROM snapshots ORDER BY kind"):
            for row in validate(kind, json.loads(data)):
                if (row["package"], row["from"], row["to"]) != (package, from_version, to_version):
                    continue
                if kind == "knowledge":
                    result["recipes"].append({"id": row["recipe"], "instruction": RECIPES[row["recipe"]], "source": row["source"]})
                else:
                    result["observations"].append({key: row[key] for key in ("check", "outcome", "source")})
                result["status"] = "found"
    return result
