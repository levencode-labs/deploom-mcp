import gzip
import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from deploom_mcp import public_cache as cache


class PublicCacheTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        db = self.root / "test.sqlite"
        with closing(sqlite3.connect(db)) as conn:
            conn.execute("CREATE TABLE advisory (name,id,severity,versions,ranges)")
            conn.execute("INSERT INTO advisory VALUES ('example','TEST-01','HIGH','[]','[]')")
            conn.execute("PRAGMA user_version=1")
            conn.commit()
        self.blob = gzip.compress(db.read_bytes(), mtime=0)
        digest = hashlib.sha256(self.blob).hexdigest()
        now = int(time.time())
        self.manifest = dict(schema=1, ecosystem="npm", source=cache.SOURCE, published=now,
                             expires=now + cache.AGE, rows=1, sha256=digest, size=len(self.blob),
                             unpacked=db.stat().st_size, asset=f"npm-{digest}.sqlite.gz",
                             tag=f"osv-{now}-{digest[:16]}")
        self.calls = []
        self.etag = '"one"'

    def fetch(self, url, limit, etag=None):
        self.calls.append((url, etag))
        raw = json.dumps(self.manifest).encode()
        base = cache.RELEASES + self.manifest["tag"] + "/"
        if url == cache.POINTER:
            if self.etag == etag:
                return None, self.etag
            return json.dumps(dict(schema=1, tag=self.manifest["tag"], manifest=base + "manifest.json",
                                   sha256=hashlib.sha256(raw).hexdigest())).encode(), self.etag
        if url == base + "manifest.json":
            return raw, None
        if url == base + self.manifest["asset"]:
            return self.blob, None
        self.fail("Unexpected HTTP request")

    def test_two_profiles_use_identical_requests_and_warm_repeat_downloads_no_asset(self):
        first, second = self.root / "profile-a", self.root / "profile-b"
        cache.refresh(first, self.fetch)
        initial = self.calls[:]
        self.calls.clear()
        cache.refresh(second, self.fetch)
        self.assertEqual(initial, self.calls)
        self.calls.clear()
        cache.refresh(first, self.fetch)
        self.assertEqual([(cache.POINTER, self.etag)], self.calls)

    def test_failed_refresh_preserves_active_database_and_pointer(self):
        root = self.root / "profile"
        cache.refresh(root, self.fetch)
        before = (root / "active.json").read_bytes()
        self.etag = '"two"'
        for key, value in (("schema", 2), ("expires", 0), ("source", "unknown"), ("rows", 0)):
            with self.subTest(key=key):
                original = self.manifest[key]
                self.manifest[key] = value
                with self.assertRaises(ValueError):
                    cache.refresh(root, self.fetch)
                self.manifest[key] = original
                self.assertEqual((root / "active.json").read_bytes(), before)
        with self.assertRaises(OSError):
            cache.refresh(root, lambda *args: (_ for _ in ()).throw(OSError("offline")))
        self.assertTrue(cache.usable(root, json.loads(before)))

    def test_partial_download_never_activates(self):
        root = self.root / "profile"
        self.blob = self.blob[:-1]
        with self.assertRaisesRegex(ValueError, "digest or size"):
            cache.refresh(root, self.fetch)
        self.assertFalse((root / "active.json").exists())
