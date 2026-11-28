"""Inspect local OSV and signed knowledge independently without network or writes."""
import argparse
import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from . import public_cache, shared_records, shared_records_v2, shared_records_v3


def unknown(reason):
    return {"status": "UNKNOWN", "reason": reason}


def osv_status(root):
    try:
        manifest = json.loads((root / "active.json").read_bytes())
        if not public_cache.usable(root, manifest):
            return unknown("SNAPSHOT_INTEGRITY_INVALID")
        details = {"edition": manifest["tag"], "sha256": manifest["sha256"],
                   "expiresAt": datetime.fromtimestamp(manifest["expires"], timezone.utc).isoformat()}
        try:
            public_cache.manifest_fields(manifest, manifest["tag"], {})
        except public_cache.CacheRefreshError as exc:
            return {**details, **unknown(exc.reason), "freshness": "expired"}
        return {**details, "status": "READY", "freshness": (
            "expiring" if manifest["expires"] - time.time() <= 2 * 86400 else "fresh")}
    except FileNotFoundError:
        return unknown("SNAPSHOT_UNAVAILABLE")
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return unknown("SNAPSHOT_INTEGRITY_INVALID")


def _connect(path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    db.execute("PRAGMA trusted_schema=OFF")
    db.execute("PRAGMA query_only=ON")
    return db


def knowledge_status(root, *, legacy=False, scoped=False):
    module = shared_records if legacy else shared_records_v3 if scoped else shared_records_v2
    path = root / (module.DATABASE if legacy else module.DB)
    if not path.is_file():
        return unknown("KNOWLEDGE_SNAPSHOT_UNAVAILABLE")
    try:
        with closing(_connect(path)) as db:
            if legacy:
                row = db.execute("SELECT data,sig,checked FROM signed WHERE id=1 AND active=1").fetchone()
                if not row:
                    return unknown("KNOWLEDGE_SNAPSHOT_INACTIVE")
                edition = module.verified(row[0], row[1])
                count = sum(len(edition[k]) for k in module.KINDS)
            else:
                row = db.execute("SELECT raw,sig,checked FROM edition WHERE id=1").fetchone()
                if not row:
                    return unknown("KNOWLEDGE_SNAPSHOT_UNAVAILABLE")
                edition = module.verified_index(row[0], row[1])
                ids = set()
                for bucket, descriptors in edition["buckets"].items():
                    for desc in descriptors:
                        shard = db.execute("SELECT data FROM shard WHERE path=?", (desc["path"],)).fetchone()
                        if shard is None:
                            raise ValueError("Missing signed shard")
                        for record in module.verified_shard(shard[0], desc, bucket):
                            if record["id"] in ids:
                                raise ValueError("Duplicate record")
                            ids.add(record["id"])
                count = len(ids)
                if count != edition["recordCount"]:
                    raise ValueError("Incomplete signed edition")
            details = {"status": "READY", "revision": edition["revision"], "records": count,
                       "checkedAt": datetime.fromtimestamp(row[2], timezone.utc).isoformat(),
                       "signatureVerified": True, "authority": "ADVISORY_ONLY"}
            if not legacy:
                details["sourceSha256"] = edition["sourceSha256"]
            return details
    except (*module.ERRORS, TypeError, KeyError, OverflowError):
        return unknown("KNOWLEDGE_INTEGRITY_INVALID")


def status_all(osv_root, records_root):
    sources = {"osv": osv_status(Path(osv_root)),
               "knowledgeV3": knowledge_status(Path(records_root), scoped=True),
               "knowledgeV2": knowledge_status(Path(records_root)),
               "knowledgeV1": knowledge_status(Path(records_root), legacy=True)}
    # v1 is a compatibility fallback; prefer v3 while retaining usable v2.
    schema = 3 if sources["knowledgeV3"]["status"] == "READY" else 2
    required = (sources["osv"], sources["knowledgeV" + str(schema)])
    ready = sum(row["status"] == "READY" for row in required)
    return {"schemaVersion": 1, "status": "READY" if ready == 2 else "PARTIAL" if ready else "UNKNOWN",
            "preferredKnowledgeSchema": schema if required[1]["status"] == "READY" else None,
            "sources": sources}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("osv_directory", type=Path)
    parser.add_argument("--records-directory", type=Path)
    args = parser.parse_args()
    report = status_all(args.osv_directory, args.records_directory or args.osv_directory.parent / "records")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
