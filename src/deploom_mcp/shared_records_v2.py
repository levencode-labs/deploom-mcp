"""Fail-closed read-only client for signed v2-native migration knowledge.

Neither a cache hit nor an agent-authored knowledge item may authorize an edit,
install, codemod execution or project-success verdict. Refresh fetches only fixed
publisher URLs; lookup never transmits names/versions to the network.
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

from .host_evidence_policy import mirror_knowledge_only
from .knowledge_contract_v2 import PKG, VERSION, validate_record, query_match, projection
from .public_cache import get
from .record_contract import parse

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
    if (not isinstance(raw, bytes) or len(raw) > INDEX_LIMIT
            or not isinstance(signature, bytes) or len(signature) != 64):
        raise ValueError("Invalid v2 signed index size")
    pin = bytes.fromhex(public_key_file.read_text(encoding="ascii").strip())
    Ed25519PublicKey.from_public_bytes(pin).verify(signature, DOMAIN + hashlib.sha256(raw).digest())
    data = parse(raw)
    if (not isinstance(data, dict)
            or set(data) != {"schema", "revision", "sourceSha256", "recordCount", "buckets"}
            or type(data["schema"]) is not int or data["schema"] != 2
            or type(data["revision"]) is not int or not 25 <= data["revision"] < 2**53
            or type(data["recordCount"]) is not int or not 1 <= data["recordCount"] <= 50_000
            or not isinstance(data["sourceSha256"], str) or not HEX.fullmatch(data["sourceSha256"])
            or not isinstance(data["buckets"], dict) or not 1 <= len(data["buckets"]) <= 256):
        raise ValueError("Invalid signed index structure")
    total, count, paths = 0, 0, set()
    for bucket, descriptors in data["buckets"].items():
        if not isinstance(bucket, str) or not BUCKET.fullmatch(bucket) or not isinstance(descriptors, list) or not descriptors:
            raise ValueError("Invalid knowledge bucket")
        for desc in descriptors:
            if not isinstance(desc, dict) or set(desc) != {"path", "sha256", "bytes", "records"}:
                raise ValueError("Invalid shard descriptor")
            sha, path = desc["sha256"], desc["path"]
            if (not isinstance(sha, str) or not HEX.fullmatch(sha)
                    or not isinstance(path, str) or path != f"shards/{bucket}/{sha}.json"
                    or path in paths
                    or type(desc["bytes"]) is not int or not 1 <= desc["bytes"] <= SHARD_LIMIT
                    or type(desc["records"]) is not int or not 1 <= desc["records"] <= 50_000):
                raise ValueError("Invalid shard filename, bytes or count")
            paths.add(path)
            total += desc["bytes"]
            count += desc["records"]
            if total > TOTAL_LIMIT or len(paths) > MAX_SHARDS:
                raise ValueError("Knowledge edition exceeds limits")
    if count != data["recordCount"]:
        raise ValueError("Knowledge record-count mismatch")
    return data


def verified_shard(raw, desc, bucket):
    if not isinstance(raw, bytes) or len(raw) != desc["bytes"] or digest(raw) != desc["sha256"]:
        raise ValueError("Knowledge shard integrity mismatch")
    data = parse(raw)
    if (not isinstance(data, dict) or set(data) != {"schema", "records"}
            or type(data["schema"]) is not int or data["schema"] != 2
            or not isinstance(data["records"], list) or len(data["records"]) != desc["records"]):
        raise ValueError("Invalid knowledge shard schema")
    seen = set()
    for record in data["records"]:
        validate_record(record)
        if hashlib.sha256(record["package"].encode("ascii")).hexdigest()[:2] != bucket:
            raise ValueError("Record belongs to another bucket")
        if record["id"] in seen:
            raise ValueError("Duplicate knowledge IDs within shard")
        seen.add(record["id"])
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
    """Download a complete publisher-chosen edition or retain the last trusted one."""
    if mirror_knowledge_only() or any(os.environ.get(flag) == "1" for flag in
           ("DEPLOOM_MCP_LOCAL_CACHE_ONLY", "DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE",
            "DEPLOOM_MCP_MIGRATION_OFFLINE")):
        return False
    return _refresh(root, lambda path, limit: fetch(BASE + path, limit)[0], if_due=if_due)


def refresh_from_mirror(root, read, *, if_due=False):
    """Import signed bytes from a host-configured mirror using relative paths."""
    if any(os.environ.get(flag) == "1" for flag in
           ("DEPLOOM_MCP_LOCAL_CACHE_ONLY", "DEPLOOM_MCP_MIGRATION_OFFLINE", "DEPLOOM_MCP_KNOWLEDGE_DISABLED")):
        return False
    return _refresh(root, read, if_due=if_due)


def _refresh(root, read, *, if_due):
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
            raw = read("index.json", INDEX_LIMIT)
            signature = read("index.sig", 64)
            index = verified_index(raw, signature)
            if old and (index["revision"] < old[0]["revision"]
                        or index["revision"] == old[0]["revision"] and raw != old[1][0]):
                raise ValueError("Signed revision rollback or substitution")
            shards = {}
            all_ids = set()
            count = 0
            for bucket, descriptors in index["buckets"].items():
                for desc in descriptors:
                    payload = read(desc["path"], desc["bytes"])
                    records = verified_shard(payload, desc, bucket)
                    for record in records:
                        if record["id"] in all_ids:
                            raise ValueError("Knowledge ID duplicated across shards")
                        all_ids.add(record["id"])
                    shards[desc["path"]] = payload
                    count += len(records)
            if count != index["recordCount"]:
                raise ValueError("Incomplete knowledge edition")
            db.execute("DELETE FROM shard")
            db.executemany("INSERT INTO shard(path,data) VALUES (?,?)", shards.items())
            db.execute("INSERT INTO edition(id,raw,sig,checked) VALUES (1,?,?,?) "
                       "ON CONFLICT(id) DO UPDATE SET raw=excluded.raw,sig=excluded.sig,checked=excluded.checked",
                       (raw, signature, int(time.time())))
            db.commit()
            return True
    except ERRORS:
        return False


def lookup(root, package, from_version, to_version, *, manager="npm"):
    """Return None only if no complete locally trusted v2 edition is available."""
    if (not isinstance(package, str) or not PKG.fullmatch(package)
            or not isinstance(from_version, str) or not VERSION.fullmatch(from_version)
            or not isinstance(to_version, str) or not VERSION.fullmatch(to_version)):
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
            result = {"ok": True, "authority": "ADVISORY_ONLY", "status": "no-matching-record",
                      "snapshotSource": "verified-pages", "snapshotSchema": 2,
                      "knowledge": [], "observations": [], "recipes": []}
            for desc in index["buckets"].get(bucket, []):
                shard = db.execute("SELECT data FROM shard WHERE path=?", (desc["path"],)).fetchone()
                if shard is None:
                    raise ValueError("Missing knowledge shard")
                for record in verified_shard(shard[0], desc, bucket):
                    strength = query_match(record, package, from_version, to_version, manager)
                    if strength:
                        result["knowledge"].append(projection(record, strength))
                        result["status"] = "found"
            return result
    except ERRORS:
        return None
