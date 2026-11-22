"""Bounded, read-only batch retrieval of signed v2 migration knowledge.

This tool is entirely local: it never calls refresh() or performs HTTP.
Every result remains ADVISORY_ONLY and cannot alter a Core verdict, held
dependency, permission, registry, or verification requirement.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from . import shared_records_v2 as signed
from .knowledge_contract_v2 import MANAGERS, PKG, SOURCE, VERSION, projection, query_match

MAX_QUERIES = 32
MAX_INPUT_BYTES = 12_288
MAX_OUTPUT_BYTES = 65_536
MAX_CARDS_PER_QUERY = 3
REQUIRED = frozenset({"package", "from_version", "to_version", "package_manager"})


def _wire_size(value):
    return len(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))


def _error(reason):
    return {
        "ok": False, "authority": "ADVISORY_ONLY", "status": "invalid-request",
        "error": {"code": "INVALID_KNOWLEDGE_BATCH", "reason": reason},
    }


def _validate(requests):
    if not isinstance(requests, list) or not 1 <= len(requests) <= MAX_QUERIES:
        raise ValueError("Expected 1..32 exact transitions")
    if _wire_size(requests) > MAX_INPUT_BYTES:
        raise ValueError("Input exceeds 12 KiB")
    for row in requests:
        if not isinstance(row, dict) or set(row) != REQUIRED:
            raise ValueError("Each query needs package, from_version, to_version, package_manager only")
        p, start, target, manager = (row[k] for k in
                                      ("package", "from_version", "to_version", "package_manager"))
        if (not isinstance(p, str) or len(p) > 180 or not PKG.fullmatch(p)
                or not isinstance(start, str) or len(start) > 70 or not VERSION.fullmatch(start)
                or not isinstance(target, str) or len(target) > 70 or not VERSION.fullmatch(target)
                or start == target or not isinstance(manager, str) or manager not in MANAGERS):
            raise ValueError("Invalid public npm identity, exact version, or package manager")
    return requests


def _private_package(package):
    """Corporate/private scope wins over public-name similarity."""
    declared = os.environ.get("DEPLOOM_MCP_PRIVATE_SCOPES", "")
    for item in declared.split(","):
        scope = item.strip().lower()
        if not scope:
            continue
        if not scope.startswith("@"):
            scope = "@" + scope
        if package == scope or package.startswith(scope.rstrip("/") + "/"):
            return True
    return False


def _status_only(requests, status, source="none"):
    rows = []
    seen = {}
    for i, query in enumerate(requests):
        identity = tuple(query[k] for k in ("package", "from_version", "to_version", "package_manager"))
        row = {"index": i, **query, "status": status, "matchingCount": 0,
               "knowledge": [], "omittedCount": 0}
        if identity in seen:
            row["status"] = "duplicate-query"
            row["duplicateOf"] = seen[identity]
        else:
            seen[identity] = i
            if _private_package(query["package"]):
                row["status"] = "skipped-private-scope"
        rows.append(row)
    return {
        "ok": True, "authority": "ADVISORY_ONLY", "status": status,
        "snapshotSource": source, "snapshotSchema": None, "queryCount": len(requests),
        "results": rows,
        "truncation": {"truncated": False, "omittedRecords": 0, "maxBytes": MAX_OUTPUT_BYTES},
        "requiresProjectVerification": True,
    }


def _bounded(response):
    omitted = sum(r["omittedCount"] for r in response["results"])
    response["truncation"]["omittedRecords"] = omitted
    response["truncation"]["truncated"] = omitted != 0
    while _wire_size(response) > MAX_OUTPUT_BYTES:
        for row in reversed(response["results"]):
            if row["knowledge"]:
                row["knowledge"].pop()
                row["omittedCount"] += 1
                break
        else:
            return {
                "ok": False, "authority": "ADVISORY_ONLY",
                "status": "response-too-large",
                "error": {"code": "KNOWLEDGE_RESPONSE_TOO_LARGE"},
            }
        omitted += 1
        response["truncation"]["omittedRecords"] = omitted
        response["truncation"]["truncated"] = True
    response["truncation"]["bytes"] = _wire_size(response)
    # Adding the byte field must never break the cap.
    if response["truncation"]["bytes"] > MAX_OUTPUT_BYTES or _wire_size(response) > MAX_OUTPUT_BYTES:
        return {
            "ok": False, "authority": "ADVISORY_ONLY", "status": "response-too-large",
            "error": {"code": "KNOWLEDGE_RESPONSE_TOO_LARGE"},
        }
    response["truncation"]["bytes"] = _wire_size(response)
    return response


def lookup_batch(root, requests):
    """Look up many transitions against one signed local edition and one SQLite view."""
    try:
        requests = _validate(requests)
    except (ValueError, TypeError, OverflowError) as exc:
        return _error(str(exc))

    if (os.environ.get("DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE") == "1"
            or os.environ.get("DEPLOOM_MCP_KNOWLEDGE_DISABLED") == "1"):
        return _bounded(_status_only(requests, "disabled-by-policy"))

    # Offline/local-only mode does NOT disable trusted already-downloaded data.
    # It only prohibits refresh; this reader performs no network calls.
    root = Path(root) if root is not None else None
    if root is None or not (root / signed.DB).is_file():
        return _bounded(_status_only(requests, "snapshot-unavailable"))

    response = _status_only(requests, "ready", source="verified-pages")
    response["snapshotSchema"] = 2
    seen = {}
    try:
        with closing(sqlite3.connect((root / signed.DB).resolve().as_uri() + "?mode=ro", uri=True)) as db:
            trusted = signed._read(db)
            if trusted is None:
                raise ValueError("Unverified edition")
            index = trusted[0]
            response["snapshotRevision"] = index["revision"]
            bucket_cache = {}
            for i, query in enumerate(requests):
                row = response["results"][i]
                if row["status"] == "skipped-private-scope":
                    continue
                identity = tuple(query[k] for k in ("package", "from_version", "to_version", "package_manager"))
                if identity in seen:
                    row["status"] = "duplicate-query"
                    row["duplicateOf"] = seen[identity]
                    continue
                seen[identity] = i
                bucket = hashlib.sha256(query["package"].encode("ascii")).hexdigest()[:2]
                if bucket not in bucket_cache:
                    records, record_ids = [], set()
                    for descriptor in index["buckets"].get(bucket, []):
                        fragment = db.execute(
                            "SELECT data FROM shard WHERE path=?", (descriptor["path"],)).fetchone()
                        if fragment is None:
                            raise ValueError("Local signed shard missing")
                        for record in signed.verified_shard(fragment[0], descriptor, bucket):
                            if record["id"] in record_ids:
                                raise ValueError("Duplicate IDs in local shard")
                            record_ids.add(record["id"])
                            records.append(record)
                    bucket_cache[bucket] = records
                findings = []
                for record in bucket_cache[bucket]:
                    strength = query_match(
                        record, query["package"], query["from_version"],
                        query["to_version"], query["package_manager"])
                    if strength:
                        card = projection(record, strength)
                        # Source commit comes from the signed, strictly validated fixture URL.
                        card["recordId"] = card["id"]
                        card["sourceCommit"] = SOURCE.fullmatch(card["evidence"]["source"]).group(1)
                        findings.append(card)
                findings.sort(key=lambda c: (
                    0 if c["matchStrength"] == "verified-exact-transition" else 1,
                    c["kind"], c["recordId"]))
                row["status"] = "found" if findings else "no-matching-record"
                row["matchingCount"] = len(findings)
                row["knowledge"] = findings[:MAX_CARDS_PER_QUERY]
                row["omittedCount"] = len(findings) - len(row["knowledge"])
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, UnicodeError, IndexError):
        # All-or-nothing: a bad shard anywhere in the requested buckets means
        # do not expose *any* tentative knowledge from this edition.
        return _bounded(_status_only(requests, "snapshot-unavailable"))
    return _bounded(response)
