"""Parallel signed editions preserve old consumers and separate trust domains."""
import copy
import hashlib
import importlib
import sqlite3
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from deploom_mcp import shared_records_v2 as v2
from deploom_mcp import shared_knowledge_batch as batch
from deploom_mcp import shared_records
import test_shared_records_v2 as fixtures
import test_shared_knowledge_batch as batch_fixtures


class SignedKnowledgeV3Tests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SharedKnowledgeV2Tests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.assertTrue(v2.refresh(self.fixture.root, self.fixture.fetch))
        self.v3 = importlib.import_module("deploom_mcp.shared_records_v3")
        self.record = copy.deepcopy(self.fixture.record)
        self.record["evidenceScope"] = {
            "schema": 1, "level": "source-pinned-metadata", "retainedInputSha256": "a" * 64,
            "archiveStatus": "UNKNOWN", "archiveReason": "MISSING_ARCHIVE_EVIDENCE",
            "projectStatus": "UNKNOWN", "conditionsRequired": True,
        }
        self._edition(30)
        self.calls = []

    def _edition(self, revision, *, domain=None):
        raw = fixtures.encode({"schema": 2, "records": [self.record]})
        digest = hashlib.sha256(raw).hexdigest()
        self.path = f"shards/{self.fixture.bucket}/{digest}.json"
        index = {"schema": 3, "revision": revision, "sourceSha256": "c" * 64,
                 "recordCount": 1, "buckets": {self.fixture.bucket: [
                     {"path": self.path, "sha256": digest, "bytes": len(raw), "records": 1}]}}
        index_raw = fixtures.encode(index)
        self.resources = {self.v3.BASE + "index.json": index_raw,
                          self.v3.BASE + "index.sig": self.fixture.key.sign(
                              (domain or self.v3.DOMAIN) + hashlib.sha256(index_raw).digest()),
                          self.v3.BASE + self.path: raw}

    def fetch(self, url, limit, etag=None):
        self.calls.append(url)
        if url.startswith(v2.BASE):
            return self.fixture.fetch(url, limit, etag)
        if url not in self.resources:
            raise HTTPError(url, 404, "Missing edition", {}, None)
        raw = self.resources[url]
        self.assertLessEqual(len(raw), limit)
        return raw, None

    def test_new_reader_prefers_scoped_v3_while_v2_bytes_stay_legacy(self):
        self.assertTrue(self.v3.refresh(self.fixture.root, self.fetch))
        batch_result = batch.lookup_batch(self.fixture.root, [batch_fixtures.query()])
        self.assertEqual(3, batch_result["snapshotSchema"])
        self.assertEqual("source-pinned-metadata", batch_result["results"][0]["knowledge"][0]["evidenceScope"]["level"])
        old_result = v2.lookup(self.fixture.root, "react-dom", "18.2.0", "18.3.1")
        self.assertEqual(2, old_result["snapshotSchema"])
        self.assertEqual("UNKNOWN", old_result["knowledge"][0]["evidenceScope"]["level"])
        self.assertEqual(3, shared_records.lookup(self.fixture.root, "react-dom", "18.2.0", "18.3.1")["snapshotSchema"])

    def test_missing_v3_refresh_falls_back_to_v2_without_any_project_query(self):
        self.resources = {}
        self.assertTrue(self.v3.refresh_preferred(self.fixture.root, self.fetch))
        result = batch.lookup_batch(self.fixture.root, [batch_fixtures.query()])
        self.assertEqual(2, result["snapshotSchema"])
        self.assertEqual("UNKNOWN", result["results"][0]["knowledge"][0]["evidenceScope"]["level"])
        self.assertTrue(all("react" not in url for url in self.calls))

    def test_signature_domains_are_not_interchangeable(self):
        self._edition(30, domain=v2.DOMAIN)
        self.assertFalse(self.v3.refresh(self.fixture.root, self.fetch))
        self.assertIsNone(self.v3.lookup(self.fixture.root, "react-dom", "18.2.0", "18.3.1"))
        self.assertEqual("found", v2.lookup(self.fixture.root, "react-dom", "18.2.0", "18.3.1")["status"])

    def test_knowledge_disabled_stops_both_public_updaters_and_background_start(self):
        with (patch.dict("os.environ", {"DEPLOOM_MCP_KNOWLEDGE_DISABLED": "1"}),
              patch.object(shared_records.threading, "Thread") as thread):
            self.assertFalse(self.v3.refresh_preferred(self.fixture.root, self.fetch))
            shared_records.start_refresh()
        self.assertEqual([], self.calls)
        thread.assert_not_called()

    def test_each_domain_enforces_its_own_revision(self):
        self.fixture.index["revision"] = 100
        self.fixture._update()
        self.assertTrue(v2.refresh(self.fixture.root, self.fixture.fetch))
        self.assertTrue(self.v3.refresh(self.fixture.root, self.fetch))
        self._edition(29)
        self.assertFalse(self.v3.refresh(self.fixture.root, self.fetch))
        with sqlite3.connect(self.fixture.root / self.v3.DB) as db:
            self.assertEqual(30, self.v3._read(db)[0]["revision"])
        with sqlite3.connect(self.fixture.root / v2.DB) as db:
            self.assertEqual(100, v2._read(db)[0]["revision"])

    def test_bad_new_v3_keeps_last_trusted_v3_and_v2_independent(self):
        self.assertTrue(self.v3.refresh(self.fixture.root, self.fetch))
        self.record["summary"] = "A new scoped observation."
        self._edition(31)
        self.resources[self.v3.BASE + self.path] = b"corrupt"
        self.assertFalse(self.v3.refresh(self.fixture.root, self.fetch))
        result = batch.lookup_batch(self.fixture.root, [batch_fixtures.query()])
        self.assertEqual(3, result["snapshotSchema"])
        self.assertEqual(30, result["snapshotRevision"])
        self.assertEqual("Source-backed mismatch evidence.", result["results"][0]["knowledge"][0]["summary"])


if __name__ == "__main__":
    unittest.main()
