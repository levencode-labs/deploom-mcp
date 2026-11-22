import json
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from deploom_mcp import shared_records as records


class SharedRecordTests(unittest.TestCase):
    def setUp(self):
        self.example = {"schema": 1, "package": "react-dom", "from": "18.2.0", "to": "18.3.1",
                        "source": "https://github.com/levencode-labs/deploom-npm/blob/" + "a" * 40 + "/fixtures/react-renderers/check.cjs"}
        self.history = [dict(self.example, check="install", outcome="passed")]
        self.recipes = [dict(self.example, recipe="align-react-renderers")]

    def test_disallow_unknown_fields_packages_sources_and_conflicts(self):
        for change in ({"notes": "freeform"}, {"package": "@example/private"}, {"source": "https://example.invalid"},
                       {"schema": 2}, {"from": "../1"}, {"check": "unchecked"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                records.validate("history", [dict(self.history[0], **change)])
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            records.validate("history", self.history + [dict(self.history[0], outcome="failed")])

    def test_public_preview_is_bounded_and_respects_local_mode(self):
        from unittest.mock import patch
        args = ("react", "18.2.0", "18.3.1", "test", "passed")
        with patch.dict(records.os.environ, {}, clear=True):
            preview = records.contribution_preview(*args)
            self.assertEqual(preview["status"], "awaiting-user")
            self.assertEqual(preview["observation"], dict(zip(("package", "from", "to", "check", "outcome"), args)))
            for index, value in enumerate(("@private/pkg", "18.2.0/path", "١.٢.٣", "notes", "unknown")):
                changed = list(args)
                changed[index] = value
                self.assertEqual(records.contribution_preview(*changed), {"status": "ineligible"})
            for flag in ("DEPLOOM_MCP_LOCAL_CACHE_ONLY", "DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE",
                         "DEPLOOM_MCP_MIGRATION_OFFLINE"):
                with patch.dict(records.os.environ, {flag: "1"}):
                    self.assertEqual(records.contribution_preview(*args), {"status": "disabled"})

    def test_signed_refresh_recovery_and_fallback(self):
        from unittest.mock import patch
        import hashlib
        import sqlite3
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key = Ed25519PrivateKey.generate()
            pin = root / "records-public.hex"
            pin.write_text(key.public_key().public_bytes_raw().hex())
            state = {"revision": 26, "schema": 1, "history": [dict(self.history[0], outcome="failed")],
                     "knowledge": self.recipes}
            def edition():
                body = json.dumps(state).encode()
                return body, key.sign(records.DOMAIN + hashlib.sha256(body).digest())
            body, sig = edition()
            calls = []
            def fetch(url, limit, etag):
                calls.append(url)
                if url == records.BASE + "cache.sig":
                    return sig, None
                self.assertEqual(url, records.BASE + "cache.json")
                return (None, etag) if etag == tag else (body, tag)
            def read():
                return records.lookup(root, "react-dom", "18.2.0", "18.3.1")
            with patch.object(records, "public_key_file", pin):
                from unittest.mock import Mock
                from deploom_mcp import public_cache
                tag = '"first"'
                blocked = Mock(side_effect=AssertionError("Unexpected network access"))
                with patch.dict("os.environ", {"DEPLOOM_MCP_LOCAL_CACHE_ONLY": "1"}), patch.object(records.threading, "Thread", blocked):
                    records.start_refresh()
                    self.assertFalse(records.refresh(root, blocked))
                    with self.assertRaisesRegex(ValueError, "Cache refresh disabled"):
                        public_cache.refresh(root, blocked)
                    self.assertEqual(read()["snapshotSource"], "bundled")
                    self.assertFalse((root / records.DATABASE).exists())
                    blocked.assert_not_called()
                self.assertEqual(read()["snapshotSource"], "bundled")
                self.assertTrue(records.refresh(root, fetch))
                self.assertEqual(read()["observations"][0]["outcome"], "failed")
                self.assertTrue(records.refresh(root, fetch))
                self.assertEqual([s.split("/")[-1] for s in calls], ["cache.json", "cache.sig", "cache.json"])
                with patch.dict("os.environ", {"DEPLOOM_MCP_LOCAL_CACHE_ONLY": "1"}):
                    self.assertFalse(records.refresh(root, blocked))
                    self.assertEqual(read()["snapshotSource"], "verified-pages")
                    blocked.assert_not_called()
                tag = '"tampered"'
                valid = body
                body += b" "
                self.assertFalse(records.refresh(root, fetch))
                self.assertEqual(read()["snapshotSource"], "bundled")
                self.assertEqual(read()["observations"][0]["outcome"], "passed")
                body = valid
                self.assertTrue(records.refresh(root, fetch))
                state["revision"] = 25
                body, sig = edition()
                tag = '"rollback"'
                self.assertFalse(records.refresh(root, fetch))
                state["revision"] = 27
                state["history"][0]["notes"] = "custom instructions"
                body, sig = edition()
                tag = '"schema"'
                self.assertFalse(records.refresh(root, fetch))
                del state["history"][0]["notes"]
                body, sig = edition()
                tag = '"recovered"'
                self.assertTrue(records.refresh(root, fetch))
                with closing(sqlite3.connect(root / records.DATABASE)) as db, db:
                    db.execute("UPDATE signed SET data=?", (b"{}",))
                self.assertEqual(read()["snapshotSource"], "bundled")
                def offline(*args):
                    raise OSError("offline")
                self.assertFalse(records.refresh(root, offline))
                self.assertEqual(read()["snapshotSource"], "bundled")

    def test_data_rejects_duplicate_keys_and_hidden_characters(self):
        with self.assertRaises(ValueError):
            records.parse(b'{"outcome":"passed","outcome":"failed"}')
        for field, value in (("source", self.example["source"] + "\n"), ("from", "1.0.0\n"),
                             ("source", "https://example.invalid/<html>"), ("recipe", "unreviewed instructions")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                records.validate("knowledge", [dict(self.recipes[0], **{field: value})])
