"""Verified, all-or-nothing read-only client for signed public v2 record shards.

Never send a package name or project data to the publisher. Refresh downloads
only publisher-chosen fixed URLs and commits a *complete* verified edition.
Lookups are exclusively local; a missing/corrupt shard fails the entire edition.
"""
import hashlib
import os
import re
import sqlite3
import time
from contextlib import closing
from http.client import HTTPException
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .public_cache import get
from .record_contract import KINDS, RECIPES, VERSION, parse, validate

BASE = "https://levencode-labs.github.io/deploom-npm/v2/"
DOMAIN = b"DepLoom public migration records v2\x00"
DB = "signed-records-v2.sqlite"
INDEX_LIMIT = 2_000_000
SHARD_LIMIT = 256_000
TOTAL_LIMIT = 32_000_000
MAX_SHARDS = 2048
HEX = re.compile(r"[a-f0-9]{64}\Z")
BUCKET = re.compile(r"[a-f0-9]{2}\Z")
ERRORS = (OSError, HTTPException, InvalidSignature, ValueError, sqlite3.Error)
public_key_file = Path(__file__).with_name("records-public.hex")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def verified_index(raw, signature):
    if not isinstance(raw, bytes) or len(raw) > INDEX_LIMIT or not isinstance(signature, bytes) or len(signature) != 64:
        raise ValueError("Invalid v2 signed index size")
    key = bytes.fromhex(public_key_file.read_text(encoding="ascii").strip())
    Ed25519PublicKey.from_public_bytes(key).verify(signature, DOMAIN + hashlib.sha256(raw).digest())
    data = parse(raw)
    if (not isinstance(data, dict)
            or set(data) != {"schema", "revision", "sourceSha256", "recordCount", "buckets"}
            or type(data["schema"]) is not int or data["schema"] != 2
            or type(data["revision"]) is not int or not 25 <= data["revision"] < 2 ** 53
            or type(data["recordCount"]) is not int or not 1 <= data["recordCount"] <= 2_000_000
            or not isinstance(data["sourceSha256"], str) or not HEX.fullmatch(data["sourceSha256"])
            or not isinstance(data["buckets"], dict) or not 1 <= len(data["buckets"]) <= 256):
        raise ValueError("Invalid v2 signed index")
    total_size, total_records, paths = 0, 0, set()
    for bucket, descriptors in data["buckets"].items():
        if not isinstance(bucket, str) or not BUCKET.fullmatch(bucket) or not isinstance(descriptors, list) or not descriptors:
            raise ValueError("Invalid bucket")
        for desc in descriptors:
            if not isinstance(desc, dict) or set(desc) != {"path", "sha256", "bytes", "records"}:
                raise ValueError("Invalid descriptor")
            sha, path = desc["sha256"], desc["path"]
            if (not isinstance(sha, str) or not HEX.fullmatch(sha)
                    or not isinstance(path, str) or path != f"shards/{bucket}/{sha}.json"
                    or path in paths or type(desc["bytes"]) is not int or not 1 <= desc["bytes"] <= SHARD_LIMIT
                    or type(desc["records"]) is not int or not 1 <= desc["records"] <= 50_000):
                raise ValueError("Invalid shard path or size")
            paths.add(path)
            total_size += desc["bytes"]
            total_records += desc["records"]
            if total_size > TOTAL_LIMIT or len(paths) > MAX_SHARDS:
                raise ValueError("v2 edition exceeds download budget")
    if total_records != data["recordCount"]:
        raise ValueError("v2 record count mismatch")
    return data


def verified_shard(raw, desc, bucket):
    if not isinstance(raw, bytes) or len(raw) != desc["bytes"] or digest(raw) != desc["sha256"]:
        raise ValueError("v2 shard digest or size mismatch")
    data = parse(raw)
    if (not isinstance(data, dict) or set(data) != {"schema", "records"}
            or type(data["schema"]) is not int or data["schema"] != 2
            or not isinstance(data["records"], list) or len(data["records"]) != desc["records"]):
        raise ValueError("Invalid v2 shard")
    grouped = {kind: [] for kind in KINDS}
    for item in data["records"]:
        if not isinstance(item, dict) or set(item) != {"kind", "record"} or item["kind"] not in KINDS:
            raise ValueError("Unknown v2 record kind")
        record = item["record"]
        if (not isinstance(record, dict) or not isinstance(record.get("package"), str)
                or hashlib.sha256(record["package"].encode("utf-8")).hexdigest()[:2] != bucket):
            raise ValueError("Wrong shard bucket")
        grouped[item["kind"]].append(record)
    for kind, rows in grouped.items():
        validate(kind, rows)
    return data["records"]


def _read(db):
    row = db.execute("SELECT raw,sig,checked FROM edition WHERE id=1").fetchone()
    if not row:
        return None
    try:
        return verified_index(row[0], row[1]), row
    except ERRORS:
        return None


def _tables(db):
    db.execute("CREATE TABLE IF NOT EXISTS edition "
               "(id INTEGER PRIMARY KEY CHECK(id=1),raw BLOB NOT NULL,sig BLOB NOT NULL,checked INTEGER NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS shard (path TEXT PRIMARY KEY,data BLOB NOT NULL)")


def refresh(root, fetch=get, *, if_due=False):
    """Refresh fixed public snapshot URLs; never upload package/project information."""
    if any(os.environ.get(flag) == "1" for flag in
           ("DEPLOOM_MCP_LOCAL_CACHE_ONLY", "DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE",
            "DEPLOOM_MCP_MIGRATION_OFFLINE")):
        return False
    try:
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(root / DB, timeout=60)) as db:
            _tables(db)
            db.commit()
            db.execute("BEGIN IMMEDIATE")
            old = _read(db)
            if old and if_due and 0 <= time.time() - old[1][2] < 86400:
                db.rollback()
                return True
            raw, _ = fetch(BASE + "index.json", INDEX_LIMIT)
            sig, _ = fetch(BASE + "index.sig", 64)
            index = verified_index(raw, sig)
            if old:
                previous = old[0]
                if (index["revision"] < previous["revision"]
                        or (index["revision"] == previous["revision"] and raw != old[1][0])):
                    raise ValueError("v2 snapshot rollback or equal-revision substitution")
            shards = {}
            count = 0
            for bucket, descriptors in index["buckets"].items():
                for desc in descriptors:
                    blob, _ = fetch(BASE + desc["path"], desc["bytes"])
                    records = verified_shard(blob, desc, bucket)
                    shards[desc["path"]] = blob
                    count += len(records)
            if count != index["recordCount"]:
                raise ValueError("Incomplete v2 edition")
            db.execute("DELETE FROM shard")
            db.executemany("INSERT INTO shard(path,data) VALUES (?,?)", shards.items())
            db.execute("INSERT INTO edition(id,raw,sig,checked) VALUES (1,?,?,?) "
                       "ON CONFLICT(id) DO UPDATE SET raw=excluded.raw,sig=excluded.sig,checked=excluded.checked",
                       (raw, sig, int(time.time())))
            db.commit()
            return True
    except ERRORS:
        return False


def lookup(root, package, from_version, to_version):
    """Return None if v2 is unavailable; do not perform HTTP from a lookup."""
    if not isinstance(package, str) or not isinstance(from_version, str) or not isinstance(to_version, str):
        return None
    if (not package.isascii() or
            not re.fullmatch(VERSION, from_version, re.ASCII)
            or not re.fullmatch(VERSION, to_version, re.ASCII)):
        return None
    if root is None or not (Path(root) / DB).is_file():
        return None
    try:
        with closing(sqlite3.connect((Path(root) / DB).resolve().as_uri() + "?mode=ro", uri=True)) as db:
            old = _read(db)
            if not old:
                return None
            index = old[0]
            bucket = hashlib.sha256(package.encode("ascii")).hexdigest()[:2]
            descriptors = index["buckets"].get(bucket, [])
            rows = []
            for desc in descriptors:
                record = db.execute("SELECT data FROM shard WHERE path=?", (desc["path"],)).fetchone()
                if not record:
                    raise ValueError("Missing v2 shard")
                rows.extend(verified_shard(record[0], desc, bucket))
            result = {"ok": True, "authority": "ADVISORY_ONLY", "status": "no-matching-record",
                      "snapshotSource": "verified-pages", "snapshotSchema": 2,
                      "observations": [], "recipes": []}
            for item in rows:
                row = item["record"]
                if (row["package"], row["from"], row["to"]) != (package, from_version, to_version):
                    continue
                if item["kind"] == "knowledge":
                    result["recipes"].append({"id": row["recipe"], "instruction": RECIPES[row["recipe"]],
                                              "source": row["source"]})
                else:
                    result["observations"].append({key: row[key] for key in ("check", "outcome", "source")})
                result["status"] = "found"
            return result
    except ERRORS:
        return None
