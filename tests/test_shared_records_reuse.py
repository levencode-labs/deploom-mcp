"""Content-addressed reuse still verifies every byte before edition activation."""
import copy
import hashlib
import sqlite3
import unittest

from deploom_mcp import shared_records_v2 as signed
import test_shared_records_v2 as fixtures


class SignedShardReuseTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SharedKnowledgeV2Tests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.records = [copy.deepcopy(self.fixture.record)]
        another = copy.deepcopy(self.fixture.record)
        another["id"] = "react-second-bucket"
        another["package"] = another["evidence"]["claim"]["package"] = "react"
        self.records.append(another)
        self._edition(29)
        self.assertTrue(signed.refresh(self.fixture.root, self.fixture.fetch))
        self.fixture.calls.clear()

    def _edition(self, revision):
        buckets = {}
        for record in self.records:
            bucket = hashlib.sha256(record["package"].encode()).hexdigest()[:2]
            buckets.setdefault(bucket, []).append(record)
        index = {"schema": 2, "revision": revision, "sourceSha256": "c" * 64,
                 "recordCount": len(self.records), "buckets": {}}
        resources = {}
        for bucket, rows in buckets.items():
            raw = fixtures.encode({"schema": 2, "records": rows})
            digest = hashlib.sha256(raw).hexdigest()
            path = f"shards/{bucket}/{digest}.json"
            resources[signed.BASE + path] = raw
            index["buckets"][bucket] = [{"path": path, "sha256": digest, "bytes": len(raw), "records": len(rows)}]
        raw = fixtures.encode(index)
        resources[signed.BASE + "index.json"] = raw
        resources[signed.BASE + "index.sig"] = self.fixture.key.sign(signed.DOMAIN + hashlib.sha256(raw).digest())
        self.fixture.resources = resources
        self.index = index

    def test_due_unchanged_edition_reads_only_index_and_signature(self):
        with sqlite3.connect(self.fixture.root / signed.DB) as db:
            db.execute("UPDATE edition SET checked=0")
        self.assertTrue(signed.refresh(self.fixture.root, self.fixture.fetch, if_due=True))
        self.assertEqual([signed.BASE + "index.json", signed.BASE + "index.sig"], self.fixture.calls)
        self.assertEqual("found", signed.lookup(self.fixture.root, "react", "18.2.0", "18.3.1")["status"])

    def test_one_changed_bucket_fetches_only_new_shard(self):
        self.records[0]["summary"] = "A separately pinned new observation."
        self._edition(30)
        self.assertTrue(signed.refresh(self.fixture.root, self.fixture.fetch))
        self.assertEqual(3, len(self.fixture.calls))
        hit = signed.lookup(self.fixture.root, "react-dom", "18.2.0", "18.3.1")
        self.assertEqual(self.records[0]["summary"], hit["knowledge"][0]["summary"])

    def test_corrupt_cached_shard_is_refetched_even_when_path_is_unchanged(self):
        bucket = hashlib.sha256(b"react-dom").hexdigest()[:2]
        path = self.index["buckets"][bucket][0]["path"]
        with sqlite3.connect(self.fixture.root / signed.DB) as db:
            db.execute("UPDATE shard SET data=? WHERE path=?", (b"corrupt", path))
        self.assertTrue(signed.refresh(self.fixture.root, self.fixture.fetch))
        self.assertEqual([signed.BASE + "index.json", signed.BASE + "index.sig", signed.BASE + path], self.fixture.calls)
        self.assertEqual("found", signed.lookup(self.fixture.root, "react-dom", "18.2.0", "18.3.1")["status"])

    def test_changed_invalid_shard_never_activates_partial_edition(self):
        self.records[0]["summary"] = "A separately pinned new observation."
        self._edition(30)
        bucket = hashlib.sha256(b"react-dom").hexdigest()[:2]
        path = self.index["buckets"][bucket][0]["path"]
        self.fixture.resources[signed.BASE + path] = b"corrupt"
        self.assertFalse(signed.refresh(self.fixture.root, self.fixture.fetch))
        with sqlite3.connect(self.fixture.root / signed.DB) as db:
            self.assertEqual(29, signed._read(db)[0]["revision"])
        self.assertEqual("Source-backed mismatch evidence.", signed.lookup(
            self.fixture.root, "react-dom", "18.2.0", "18.3.1")["knowledge"][0]["summary"])


if __name__ == "__main__":
    unittest.main()
