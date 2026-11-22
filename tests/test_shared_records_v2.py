import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from deploom_mcp import shared_records_v2 as v2
from deploom_mcp import shared_records


def encode(data):
    return (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode()


class SharedRecordsV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = Ed25519PrivateKey.generate()
        pub = self.key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
        self.pin = self.root / "pin.hex"
        self.pin.write_text(pub.hex())
        self.original_pin = v2.public_key_file
        v2.public_key_file = self.pin
        self.addCleanup(setattr, v2, "public_key_file", self.original_pin)
        rows = shared_records.bundled()
        self.package = "react-dom"
        self.bucket = hashlib.sha256(self.package.encode()).hexdigest()[:2]
        self.shard = encode({"schema": 2, "records": [
            {"kind": kind, "record": item} for kind in ("history", "knowledge") for item in rows[kind]]})
        self.shard_sha = hashlib.sha256(self.shard).hexdigest()
        self.path = f"shards/{self.bucket}/{self.shard_sha}.json"
        self.index = {"schema": 2, "revision": 29, "sourceSha256": "c" * 64,
                      "recordCount": 2, "buckets": {self.bucket: [
                          {"path": self.path, "sha256": self.shard_sha,
                           "bytes": len(self.shard), "records": 2}]}}
        self._update()
        self.calls = []

    def _update(self):
        self.raw = encode(self.index)
        self.signature = self.key.sign(v2.DOMAIN + hashlib.sha256(self.raw).digest())
        self.resources = {
            v2.BASE + "index.json": self.raw,
            v2.BASE + "index.sig": self.signature,
            v2.BASE + self.path: self.shard,
        }

    def fetch(self, url, limit, etag=None):
        self.calls.append(url)
        data = self.resources[url]
        if len(data) > limit:
            raise ValueError("Over limit")
        return data, None

    def test_refresh_and_exact_lookup_offline(self):
        self.assertTrue(v2.refresh(self.root, self.fetch))
        self.calls.clear()
        hit = v2.lookup(self.root, "react-dom", "18.2.0", "18.3.1")
        self.assertEqual("verified-pages", hit["snapshotSource"])
        self.assertEqual(2, hit["snapshotSchema"])
        self.assertEqual("found", hit["status"])
        self.assertEqual("align-react-renderers", hit["recipes"][0]["id"])
        self.assertEqual([], self.calls)
        miss = v2.lookup(self.root, "react-dom", "18.2.0", "19.0.0")
        self.assertEqual("no-matching-record", miss["status"])
        self.assertEqual([], self.calls)

    def test_signature_tamper_never_activates(self):
        self.resources[v2.BASE + "index.sig"] = b"\0" * 64
        self.assertFalse(v2.refresh(self.root, self.fetch))
        self.assertIsNone(v2.lookup(self.root, "react-dom", "18.2.0", "18.3.1"))

    def test_partial_or_mutated_shard_retains_last_good_edition(self):
        self.assertTrue(v2.refresh(self.root, self.fetch))
        self.index["revision"] = 30
        self._update()
        self.resources[v2.BASE + self.path] = self.shard[:-1]
        self.assertFalse(v2.refresh(self.root, self.fetch))
        self.assertEqual("found", v2.lookup(self.root, "react-dom", "18.2.0", "18.3.1")["status"])

    def test_revision_rollback_and_equal_revision_substitution(self):
        self.assertTrue(v2.refresh(self.root, self.fetch))
        self.index["revision"] = 28
        self._update()
        self.assertFalse(v2.refresh(self.root, self.fetch))
        self.index["revision"] = 29
        self.index["sourceSha256"] = "e" * 64
        self._update()
        self.assertFalse(v2.refresh(self.root, self.fetch))
        self.assertEqual("found", v2.lookup(self.root, "react-dom", "18.2.0", "18.3.1")["status"])

    def test_rejects_traversal_and_wrong_bucket(self):
        broken = json.loads(self.raw)
        broken["buckets"][self.bucket][0]["path"] = "../escape.json"
        malicious = encode(broken)
        signature = self.key.sign(v2.DOMAIN + hashlib.sha256(malicious).digest())
        with self.assertRaises(ValueError):
            v2.verified_index(malicious, signature)
        with self.assertRaises(ValueError):
            v2.verified_shard(self.shard, self.index["buckets"][self.bucket][0], "ff" if self.bucket != "ff" else "ee")

    def test_env_local_only_disables_network(self):
        from unittest.mock import patch
        with patch.dict("os.environ", {"DEPLOOM_MCP_LOCAL_CACHE_ONLY": "1"}):
            self.assertFalse(v2.refresh(self.root, self.fetch))
        self.assertEqual([], self.calls)


if __name__ == "__main__":
    unittest.main()
