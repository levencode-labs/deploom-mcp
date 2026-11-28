import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from deploom_mcp import shared_records_v2 as v2
from deploom_mcp import knowledge_contract_v2


def encode(data):
    return (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode()


class SharedKnowledgeV2Tests(unittest.TestCase):
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
        self.record = {
            "schema": 2, "id": "react-dom-peer-renderer-alignment-18",
            "kind": "incompatibility", "package": "react-dom",
            "fromMajor": 18, "toMajor": 18, "packageManagers": ["npm"],
            "verifiedTransitions": [{"from": "18.2.0", "to": "18.3.1"}],
            "title": "Renderer peer requirements",
            "summary": "Source-backed mismatch evidence.",
            "conditions": ["React DOM renderer has a peer requirement."],
            "actions": ["Only align the React provider if host scope permits."],
            "verification": ["Strict frozen install", "Renderer tests"],
            "negativeControls": [{"name": "mismatch", "expected": "reject",
                                  "fixtureAssertion": "ERESOLVE"}],
            "evidence": {
                "source": "https://github.com/levencode-labs/deploom-npm/blob/" +
                          "36216f29d52bacd92e0b64e87433c8ed3e84b5a4/fixtures/react-renderers/check.cjs",
                "fixtureSha256": "a" * 64,
                "claim": {"package": "react-dom", "from": "18.2.0",
                          "to": "18.3.1", "check": "install", "outcome": "passed"}
            }
        }
        knowledge_contract_v2.validate_record(self.record)
        self.bucket = hashlib.sha256(b"react-dom").hexdigest()[:2]
        self.shard = encode({"schema": 2, "records": [self.record]})
        self.shard_sha = hashlib.sha256(self.shard).hexdigest()
        self.path = f"shards/{self.bucket}/{self.shard_sha}.json"
        self.index = {"schema": 2, "revision": 29, "sourceSha256": "c" * 64,
                      "recordCount": 1, "buckets": {self.bucket: [
                          {"path": self.path, "sha256": self.shard_sha,
                           "bytes": len(self.shard), "records": 1}]}}
        self._update()
        self.calls = []

    def _update(self):
        self.raw = encode(self.index)
        self.signature = self.key.sign(v2.DOMAIN + hashlib.sha256(self.raw).digest())
        self.resources = {v2.BASE + "index.json": self.raw,
                          v2.BASE + "index.sig": self.signature,
                          v2.BASE + self.path: self.shard}

    def fetch(self, url, limit, etag=None):
        self.calls.append(url)
        raw = self.resources[url]
        if len(raw) > limit:
            raise ValueError("oversized")
        return raw, None

    def test_configured_mirror_preserves_external_and_offline_policy(self):
        def read(path, limit):
            return self.fetch(v2.BASE + path, limit)[0]

        with patch.dict("os.environ", {
            "DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE": "1",
            "DEPLOOM_MCP_LOCAL_CACHE_ONLY": "0",
            "DEPLOOM_MCP_MIGRATION_OFFLINE": "0",
        }):
            self.assertFalse(v2.refresh(self.root, self.fetch))
            self.assertEqual(self.calls, [])
            self.assertTrue(v2.refresh_from_mirror(self.root, read))
            self.assertEqual(v2.lookup(self.root, "react-dom", "18.2.0", "18.3.1")["snapshotSource"],
                             "verified-pages")
            self.calls.clear()
            for flag in ("DEPLOOM_MCP_LOCAL_CACHE_ONLY", "DEPLOOM_MCP_MIGRATION_OFFLINE"):
                with patch.dict("os.environ", {flag: "1"}):
                    self.assertFalse(v2.refresh_from_mirror(self.root, read))
                    self.assertEqual(self.calls, [])

    def test_host_mirror_mode_reads_signed_queries_without_public_fallback(self):
        from deploom_mcp import shared_records
        from deploom_mcp.shared_knowledge_batch import lookup_batch

        def read(path, limit):
            return self.fetch(v2.BASE + path, limit)[0]

        query = dict(package="react-dom", from_version="18.2.0", to_version="18.3.1", package_manager="npm")
        with patch.dict("os.environ", {"DEPLOOM_MCP_RECORDS_MIRROR_ONLY": "1",
                                      "DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE": "0"}):
            self.assertFalse(v2.refresh(self.root, self.fetch))
            self.assertFalse(shared_records.refresh(self.root, self.fetch))
            self.assertEqual(self.calls, [])
            self.assertEqual(shared_records.lookup(self.root, "react-dom", "18.2.0", "18.3.1")["status"],
                             "snapshot-unavailable")
            self.assertTrue(v2.refresh_from_mirror(self.root, read))
            self.calls.clear()
            with patch.dict("os.environ", {"DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE": "1"}):
                hit = shared_records.lookup(self.root, "react-dom", "18.2.0", "18.3.1")
                self.assertEqual(hit["snapshotSource"], "verified-pages")
                batch = lookup_batch(self.root, [query])
                self.assertEqual(batch["results"][0]["status"], "found")
                with patch.dict("os.environ", {"DEPLOOM_MCP_KNOWLEDGE_DISABLED": "1"}):
                    self.assertFalse(v2.refresh_from_mirror(self.root, read))
                    self.assertEqual(lookup_batch(self.root, [query])["status"], "disabled-by-policy")
            self.assertEqual(self.calls, [])

    def test_source_exact_and_major_candidacy_without_network(self):
        self.assertTrue(v2.refresh(self.root, self.fetch))
        self.calls.clear()
        hit = v2.lookup(self.root, "react-dom", "18.2.0", "18.3.1")
        self.assertEqual("found", hit["status"])
        self.assertEqual("verified-pages", hit["snapshotSource"])
        self.assertEqual(2, hit["snapshotSchema"])
        self.assertEqual([], hit["recipes"])
        self.assertEqual("incompatibility", hit["knowledge"][0]["kind"])
        self.assertEqual("verified-exact-transition", hit["knowledge"][0]["matchStrength"])
        candidate = v2.lookup(self.root, "react-dom", "18.1.0", "18.3.2")
        self.assertEqual("major-line-candidate", candidate["knowledge"][0]["matchStrength"])
        self.assertTrue(candidate["knowledge"][0]["requiresProjectVerification"])
        composed = v2.lookup(self.root, "react-dom", "17.0.0", "18.3.1")
        self.assertEqual("intermediate-transition-candidate", composed["knowledge"][0]["matchStrength"])
        self.assertTrue(composed["knowledge"][0]["requiresProjectVerification"])
        self.assertEqual("no-matching-record", v2.lookup(self.root, "react-dom", "17.0.0", "18.0.0")["status"])
        self.assertEqual([], self.calls)

    def test_signature_corruption_rejected(self):
        self.resources[v2.BASE + "index.sig"] = b"\0" * 64
        self.assertFalse(v2.refresh(self.root, self.fetch))
        self.assertIsNone(v2.lookup(self.root, "react-dom", "18.2.0", "18.3.1"))

    def test_bad_shard_keeps_previous_trusted_edition(self):
        self.assertTrue(v2.refresh(self.root, self.fetch))
        self.index["revision"] = 30
        # A valid local content-addressed shard is reusable. Exercise a genuinely
        # new descriptor so the corrupted response must be fetched and rejected.
        self.record["summary"] = "A new source-exact observation."
        self.shard = encode({"schema": 2, "records": [self.record]})
        self.shard_sha = hashlib.sha256(self.shard).hexdigest()
        self.path = f"shards/{self.bucket}/{self.shard_sha}.json"
        self.index["buckets"][self.bucket] = [{"path": self.path, "sha256": self.shard_sha,
                                               "bytes": len(self.shard), "records": 1}]
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
        self.index["sourceSha256"] = "d" * 64
        self._update()
        self.assertFalse(v2.refresh(self.root, self.fetch))
        self.assertEqual("found", v2.lookup(self.root, "react-dom", "18.2.0", "18.3.1")["status"])

    def test_wrong_bucket_and_traversal_rejected(self):
        wrong = json.loads(self.raw)
        wrong["buckets"][self.bucket][0]["path"] = "../escape.json"
        bad = encode(wrong)
        signature = self.key.sign(v2.DOMAIN + hashlib.sha256(bad).digest())
        with self.assertRaises(ValueError):
            v2.verified_index(bad, signature)
        wrong_bucket = "ff" if self.bucket != "ff" else "ee"
        with self.assertRaises(ValueError):
            v2.verified_shard(self.shard, self.index["buckets"][self.bucket][0], wrong_bucket)

    def test_private_mode_has_no_external_fetches(self):
        with patch.dict("os.environ", {"DEPLOOM_MCP_LOCAL_CACHE_ONLY": "1"}):
            self.assertFalse(v2.refresh(self.root, self.fetch))
        self.assertEqual([], self.calls)


if __name__ == "__main__":
    unittest.main()

