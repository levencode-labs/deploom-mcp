import json
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

    def test_refresh_reads_only_two_fixed_resources_and_preserves_good_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = []
            def fetch(url, limit, etag):
                calls.append(url)
                if etag == '"v1"':
                    return None, etag
                return json.dumps(self.history if url.endswith("history.json") else self.recipes).encode(), '"v1"'
            records.refresh(root, fetch)
            records.refresh(root, fetch)
            self.assertEqual(calls, [records.BASE + s + ".json" for s in ("history", "knowledge")]*2)
            data = records.lookup(root, "react-dom", "18.2.0", "18.3.1")
            self.assertEqual(data["authority"], "ADVISORY_ONLY")
            self.assertEqual(data["observations"][0]["outcome"], "passed")
            self.assertEqual(data["recipes"][0]["id"], "align-react-renderers")
            with self.assertRaises(ValueError):
                records.refresh(root, lambda url, limit, tag: (b'[]' if url.endswith("history.json") else b'[{"schema":2}]', '"invalid"'))
            self.assertEqual(records.lookup(root, "react-dom", "18.2.0", "18.3.1"), data)
            self.assertEqual(records.lookup(root, "other", "1.0.0", "2.0.0")["status"], "no-matching-record")
