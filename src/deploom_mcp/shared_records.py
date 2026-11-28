"""Signed advisory snapshots for public migration examples."""
import hashlib
import os
import re
import sqlite3
import threading
import time
from contextlib import closing
from http.client import HTTPException
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from deploom_mcp.host_evidence_policy import cached_knowledge_disabled, mirror_knowledge_only

from .public_cache import get
from .record_contract import CHECKS, KINDS, LIMIT, PACKAGES, RECIPES, VERSION, parse, validate

BASE = "https://levencode-labs.github.io/deploom-npm/v1/"
DOMAIN = b"DepLoom public migration records v1\x00"
public_key_file = Path(__file__).with_name("records-public.hex")
DATABASE = "signed-records-v1.sqlite"
ERRORS = (OSError, HTTPException, InvalidSignature, ValueError, sqlite3.Error)


def verified(raw, signature):
    if not isinstance(raw, bytes) or len(raw) > LIMIT or not isinstance(signature, bytes) or len(signature) != 64:
        raise ValueError("Invalid signed snapshot")
    key = bytes.fromhex(public_key_file.read_text(encoding="ascii").strip())
    Ed25519PublicKey.from_public_bytes(key).verify(signature, DOMAIN + hashlib.sha256(raw).digest())
    data = parse(raw)
    if (not isinstance(data, dict) or set(data) != {"schema", "revision", *KINDS} or
            type(data["schema"]) is not int or data["schema"] != 1 or type(data["revision"]) is not int or
            not 25 <= data["revision"] <= 2**53 - 1):
        raise ValueError("Invalid snapshot header")
    for kind in KINDS:
        validate(kind, data[kind])
    return data


def bundled():
    file = Path(__file__).with_name("records-seed.json")
    data = parse(file.read_bytes())
    return {kind: validate(kind, data[kind]) for kind in KINDS}


def refresh(root, fetch=get, *, if_due=False):
    if mirror_knowledge_only() or os.environ.get("DEPLOOM_MCP_LOCAL_CACHE_ONLY") == "1":
        return False
    try:
        root.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(root / DATABASE, timeout=60)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS signed (id INTEGER PRIMARY KEY CHECK(id=1), data BLOB, "
                       "sig BLOB, etag TEXT, active INTEGER, checked INTEGER)")
            old = db.execute("SELECT data,sig,etag,active,checked FROM signed WHERE id=1").fetchone()
            if if_due and old and 0 <= time.time() - old[4] < 86400:
                return bool(old[3])
            previous = None
            if old and old[0]:
                try:
                    previous = verified(old[0], old[1])
                except ERRORS:
                    pass
            try:
                raw, tag = fetch(BASE + "cache.json", LIMIT, old[2] if previous else None)
                if raw is None:
                    if not previous:
                        raise ValueError("Unexpected conditional response")
                    raw, sig = old[:2]
                else:
                    sig, _ = fetch(BASE + "cache.sig", 64, None)
                    data = verified(raw, sig)
                    if previous and (data["revision"] < previous["revision"] or
                                     (data["revision"] == previous["revision"] and raw != old[0])):
                        raise ValueError("Snapshot revision regression")
            except ERRORS:
                db.execute("INSERT INTO signed(id,active,checked) VALUES (1,0,?) "
                           "ON CONFLICT(id) DO UPDATE SET active=0, checked=excluded.checked", (int(time.time()),))
                return False
            db.execute("INSERT OR REPLACE INTO signed VALUES(1,?,?,?,1,?)", (raw, sig, tag, int(time.time())))
            return True
    except ERRORS:
        return False


def directory():
    configured = os.environ.get("DEPLOOM_MCP_RECORDS_DIR")
    return Path(configured) if configured else Path.home() / ".cache" / "deploom" / "records"


def start_refresh():
    if (mirror_knowledge_only()
            or os.environ.get("DEPLOOM_MCP_LOCAL_CACHE_ONLY") == "1"
            or os.environ.get("DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE") == "1"
            or os.environ.get("DEPLOOM_MCP_KNOWLEDGE_DISABLED") == "1"):
        return
    def update():
        from .shared_records_v3 import refresh_preferred
        while True:
            refresh_preferred(directory(), if_due=True)
            time.sleep(86400)
    threading.Thread(target=update, daemon=True, name="deploom-records").start()


def lookup(root, package, from_version, to_version):
    if cached_knowledge_disabled():
        return {"ok": True, "authority": "ADVISORY_ONLY", "status": "disabled-by-policy",
                "snapshotSource": "none", "knowledge": [], "observations": [], "recipes": []}
    from .shared_records_v3 import lookup as lookup_v3
    v3 = lookup_v3(root, package, from_version, to_version)
    if v3 is not None:
        return v3
    from .shared_records_v2 import lookup as lookup_v2
    v2 = lookup_v2(root, package, from_version, to_version)
    if v2 is not None:
        return v2
    if mirror_knowledge_only():
        return {"ok": True, "authority": "ADVISORY_ONLY", "status": "snapshot-unavailable",
                "snapshotSource": "none", "knowledge": [], "observations": [], "recipes": []}
    data = None
    if root is not None and (root / DATABASE).is_file():
        try:
            with closing(sqlite3.connect((root / DATABASE).resolve().as_uri() + "?mode=ro", uri=True)) as db:
                row = db.execute("SELECT data,sig FROM signed WHERE id=1 AND active=1").fetchone()
                if row:
                    data = verified(*row)
        except ERRORS:
            pass
    result = {"ok": True, "authority": "ADVISORY_ONLY", "status": "no-matching-record",
              "snapshotSource": "verified-pages" if data is not None else "bundled", "observations": [], "recipes": []}
    if data is None:
        data = bundled()
    for kind in KINDS:
        for row in data[kind]:
            if (row["package"], row["from"], row["to"]) != (package, from_version, to_version):
                continue
            if kind == "knowledge":
                result["recipes"].append({"id": row["recipe"], "instruction": RECIPES[row["recipe"]], "source": row["source"]})
            else:
                result["observations"].append({key: row[key] for key in ("check", "outcome", "source")})
            result["status"] = "found"
    return result

