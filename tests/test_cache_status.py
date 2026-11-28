"""Status checks inspect actual signed data and never contact the publisher."""
import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from deploom_mcp import cache_status, public_cache, shared_records_v2
import test_public_cache as osv_fixtures
import test_shared_records_v2 as knowledge_fixtures


class IndependentCacheStatusTests(unittest.TestCase):
    def setUp(self):
        self.osv = osv_fixtures.PublicCacheTests()
        self.osv.setUp()
        self.addCleanup(self.osv.doCleanups)
        self.knowledge = knowledge_fixtures.SharedKnowledgeV2Tests()
        self.knowledge.setUp()
        self.addCleanup(self.knowledge.doCleanups)
        self.osv_dir = self.osv.root / "profile"
        self.records_dir = self.osv.root / "records"

    def prepare(self):
        public_cache.refresh(self.osv_dir, self.osv.fetch)
        self.assertTrue(shared_records_v2.refresh(self.records_dir, self.knowledge.fetch))

    def test_no_network_no_files_for_missing_caches(self):
        with patch("urllib.request.build_opener", side_effect=AssertionError("Network forbidden")):
            report = cache_status.status_all(self.osv_dir, self.records_dir)
        self.assertEqual(report["status"], "UNKNOWN")
        self.assertFalse(self.osv_dir.exists())
        self.assertFalse(self.records_dir.exists())

    def test_verified_sources_ready_without_legacy_fallback(self):
        self.prepare()
        before = {str(p): p.read_bytes() for p in self.osv.root.rglob("*") if p.is_file()}
        report = cache_status.status_all(self.osv_dir, self.records_dir)
        self.assertEqual(report["status"], "READY")
        self.assertEqual(report["sources"]["knowledgeV2"]["revision"], self.knowledge.index["revision"])
        self.assertTrue(report["sources"]["knowledgeV2"]["signatureVerified"])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.osv.root.rglob("*") if p.is_file()})

    def test_expired_osv_does_not_hide_valid_knowledge(self):
        self.prepare()
        with patch("time.time", return_value=self.osv.manifest["expires"]):
            report = cache_status.status_all(self.osv_dir, self.records_dir)
        self.assertEqual(report["status"], "PARTIAL")
        self.assertEqual(report["sources"]["osv"]["reason"], "SNAPSHOT_STALE")
        self.assertEqual(report["sources"]["knowledgeV2"]["status"], "READY")

    def test_bad_local_shard_fails_knowledge_integrity_only(self):
        self.prepare()
        with sqlite3.connect(self.records_dir / shared_records_v2.DB) as db:
            db.execute("UPDATE shard SET data=?", (b"{}",))
        report = cache_status.status_all(self.osv_dir, self.records_dir)
        self.assertEqual(report["sources"]["osv"]["status"], "READY")
        self.assertEqual(report["sources"]["knowledgeV2"]["reason"], "KNOWLEDGE_INTEGRITY_INVALID")

    def test_scoped_status_prefers_verified_v3_and_falls_back_after_corruption(self):
        import test_shared_records_v3 as scoped_fixtures
        fixture = scoped_fixtures.SignedKnowledgeV3Tests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        public_cache.refresh(self.osv_dir, self.osv.fetch)
        self.assertTrue(fixture.v3.refresh(fixture.fixture.root, fixture.fetch))
        report = cache_status.status_all(self.osv_dir, fixture.fixture.root)
        self.assertEqual(report["status"], "READY")
        self.assertEqual(report["preferredKnowledgeSchema"], 3)
        self.assertEqual(report["sources"]["knowledgeV3"]["revision"], 30)
        with sqlite3.connect(fixture.fixture.root / fixture.v3.DB) as db:
            db.execute("UPDATE shard SET data=?", (b"{}",))
        report = cache_status.status_all(self.osv_dir, fixture.fixture.root)
        self.assertEqual(report["preferredKnowledgeSchema"], 2)
        self.assertEqual(report["sources"]["knowledgeV3"]["reason"], "KNOWLEDGE_INTEGRITY_INVALID")
        self.assertEqual(report["sources"]["knowledgeV2"]["status"], "READY")


if __name__ == "__main__":
    unittest.main()
