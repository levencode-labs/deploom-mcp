"""Refresh the public npm OSV edition. Network requests never contain project data."""
import argparse
import gzip
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import time
from contextlib import closing
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

POINTER = "https://levencode-labs.github.io/deploom-npm/v1/osv.json"
RELEASES = "https://github.com/levencode-labs/deploom-npm/releases/download/"
SOURCE = "https://osv-vulnerabilities.storage.googleapis.com/npm/all.zip"
AGE = 7 * 86400
SHA = re.compile("[a-f0-9]{64}")


class CacheRefreshError(ValueError):
    """A stable diagnostic for a refresh that must not activate an edition."""

    def __init__(self, reason, message=None):
        self.reason = reason
        super().__init__(message or "Public cache refresh: " + reason)


class ReleaseRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        dest = urlsplit(newurl)
        if dest.scheme != "https" or dest.netloc not in {"github.com", "release-assets.githubusercontent.com"}:
            raise ValueError("Unexpected release redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def get(url, limit, etag=None):
    headers = {"If-None-Match": etag} if etag else {}
    request = Request(url, headers=headers)
    try:
        response = build_opener(ReleaseRedirects()).open(request, timeout=45)
    except HTTPError as exc:
        if exc.code != 304 or not etag:
            raise
        exc.close()
        return None, etag
    with response:
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Public cache response exceeds limit")
        return data, response.headers.get("ETag")


def manifest_fields(m, tag, previous):
    for field in ("schema", "published", "expires", "rows", "size", "unpacked"):
        if type(m.get(field)) is not int:
            raise ValueError("Invalid OSV manifest field: " + field)
    sha = m.get("sha256", "")
    now = time.time()
    if (m["schema"] != 1 or m["ecosystem"] != "npm" or m["source"] != SOURCE
            or not isinstance(sha, str) or not SHA.fullmatch(sha)
            or m["tag"] != tag or tag != f"osv-{m['published']}-{sha[:16]}"
            or m["asset"] != f"npm-{sha}.sqlite.gz"
            or not 0 < m["size"] <= 64_000_000 or not 0 < m["unpacked"] <= 256_000_000
            or m["rows"] < 1 or m["expires"] != m["published"] + AGE
            or now < m["published"] - 300
            or m["published"] < previous.get("published", 0)
            or (m["published"] == previous.get("published") and previous.get("sha256") != sha)):
        raise ValueError("Invalid, stale, or regressing OSV edition")
    if now >= m["expires"]:
        raise CacheRefreshError("SNAPSHOT_STALE")


def install_database(blob, m, path):
    if len(blob) != m["size"] or hashlib.sha256(blob).hexdigest() != m["sha256"]:
        raise ValueError("OSV compressed digest or size mismatch")
    digest = hashlib.sha256()
    with gzip.GzipFile(fileobj=io.BytesIO(blob)) as source, path.open("xb") as output:
        while chunk := source.read(1024 * 1024):
            if output.tell() + len(chunk) > m["unpacked"]:
                raise ValueError("OSV decompression exceeds limit")
            output.write(chunk)
            digest.update(chunk)
        if output.tell() != m["unpacked"]:
            raise ValueError("Partial OSV database")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        conn.execute("PRAGMA trusted_schema=OFF")
        if (conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]
                or conn.execute("PRAGMA user_version").fetchone() != (1,)
                or conn.execute("SELECT type FROM sqlite_schema WHERE name='advisory'").fetchone() != ("table",)
                or conn.execute("SELECT COUNT(*) FROM advisory").fetchone() != (m["rows"],)):
            raise ValueError("Invalid OSV SQLite database")
        conn.execute("SELECT name,id,severity,versions,ranges FROM advisory LIMIT 0")
    return digest.hexdigest()


def usable(root, m):
    sha = m.get("sha256")
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        return False
    path = root / (sha + ".sqlite")
    if not path.is_file() or path.stat().st_size != m.get("unpacked"):
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == m.get("databaseSha256")


def refresh(root, fetch=get):
    if os.environ.get("DEPLOOM_MCP_LOCAL_CACHE_ONLY") == "1":
        raise CacheRefreshError("REFRESH_DISABLED_BY_POLICY", "Cache refresh disabled by DEPLOOM_MCP_LOCAL_CACHE_ONLY")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(root / "update-lock.sqlite", timeout=60)) as lock:
        lock.execute("BEGIN IMMEDIATE")
        active = root / "active.json"
        before = json.loads(active.read_bytes()) if active.is_file() else {}
        healthy = usable(root, before)
        body, etag = fetch(POINTER, 4096, before.get("pointerETag") if healthy else None)
        if body is None:
            if not healthy:
                raise ValueError("Received 304 without a usable local edition")
            manifest_fields(before, before["tag"], before)
            return before["tag"]
        pointer = json.loads(body)
        tag = pointer["tag"]
        if (type(pointer["schema"]) is not int or pointer["schema"] != 1 or
                not isinstance(tag, str) or not re.fullmatch(r"osv-[0-9]{1,12}-[a-f0-9]{16}", tag)):
            raise ValueError("Invalid public cache pointer")
        release = RELEASES + tag + "/"
        if pointer["manifest"] != release + "manifest.json":
            raise ValueError("Unexpected release manifest URL")
        raw, _ = fetch(release + "manifest.json", 8192)
        if hashlib.sha256(raw).hexdigest() != pointer["sha256"]:
            raise ValueError("OSV manifest digest mismatch")
        m = json.loads(raw)
        manifest_fields(m, tag, before)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            stage = Path(tmp)
            if healthy and m["sha256"] == before["sha256"]:
                m["databaseSha256"] = before["databaseSha256"]
            else:
                blob, _ = fetch(release + m["asset"], m["size"])
                db = stage / "index.sqlite"
                m["databaseSha256"] = install_database(blob, m, db)
                os.replace(db, root / (m["sha256"] + ".sqlite"))
            m["pointerETag"] = etag
            staged = stage / "active.json"
            staged.write_text(json.dumps(m, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(staged, active)
        return tag


def refresh_all(root):
    """Refresh independent editions and preserve each source's failure status."""
    from .shared_records import refresh as refresh_records
    from .shared_records_v2 import refresh as refresh_records_v2
    from .shared_records_v3 import refresh as refresh_records_v3

    root = Path(root)
    records = Path(os.environ.get("DEPLOOM_MCP_RECORDS_DIR") or root.parent / "records")
    sources = {}
    errors = (OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.Error, HTTPException)
    try:
        sources["osv"] = {"status": "READY", "edition": refresh(root)}
    except errors as exc:
        sources["osv"] = {
            "status": "UNKNOWN",
            "reason": exc.reason if isinstance(exc, CacheRefreshError) else "SNAPSHOT_UNAVAILABLE",
        }
    for name, update in (("knowledgeV3", refresh_records_v3), ("knowledgeV2", refresh_records_v2),
                         ("knowledgeV1", refresh_records)):
        try:
            ready = update(records) is True
        except errors:
            ready = False
        sources[name] = ({"status": "READY"} if ready else
                         {"status": "UNKNOWN", "reason": "KNOWLEDGE_REFRESH_FAILED"})
    ready_count = sum(row["status"] == "READY" for row in sources.values())
    status = "READY" if ready_count == len(sources) else "PARTIAL" if ready_count else "FAILED"
    return {"schemaVersion": 1, "ok": status == "READY", "status": status, "sources": sources}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache_directory", type=Path)
    report = refresh_all(parser.parse_args(argv).cache_directory)
    print(json.dumps(report, sort_keys=True))
    return {"READY": 0, "PARTIAL": 1, "FAILED": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())

