"""All manual knowledge refresh routes honor the same host network prohibitions."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from deploom_mcp import public_cache, shared_records


FLAGS = ("DEPLOOM_MCP_LOCAL_CACHE_ONLY", "DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE",
         "DEPLOOM_MCP_MIGRATION_OFFLINE", "DEPLOOM_MCP_KNOWLEDGE_DISABLED",
         "DEPLOOM_MCP_RECORDS_MIRROR_ONLY")


class LegacyKnowledgePolicyTests(unittest.TestCase):
    def test_each_host_prohibition_blocks_legacy_refresh_before_http(self):
        for flag in FLAGS:
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as temporary:
                settings = {name: "0" for name in FLAGS}
                settings[flag] = "1"
                fetch = Mock(side_effect=OSError("Network must not be attempted"))
                root = Path(temporary) / "records"
                with patch.dict(os.environ, settings):
                    self.assertFalse(shared_records.refresh(root, fetch))
                fetch.assert_not_called()
                self.assertFalse(root.exists(), "Policy denial must precede cache mutations too")

    def test_four_source_manual_route_cannot_bypass_knowledge_prohibition(self):
        for flag in FLAGS:
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as temporary:
                settings = {name: "0" for name in FLAGS}
                settings.update({flag: "1", "DEPLOOM_MCP_RECORDS_DIR": str(Path(temporary) / "records")})
                # OSV has its own policy scope. Leave the three real knowledge
                # refreshers in place, and intercept their shared HTTP adapter.
                with (patch.dict(os.environ, settings),
                      patch.object(public_cache, "refresh", return_value="fixture-osv-edition"),
                      patch.object(public_cache, "build_opener", side_effect=AssertionError("Unexpected HTTP")) as http):
                    result = public_cache.refresh_all(Path(temporary) / "profile")
                self.assertEqual("PARTIAL", result["status"])
                for name in ("knowledgeV1", "knowledgeV2", "knowledgeV3"):
                    self.assertEqual("UNKNOWN", result["sources"][name]["status"])
                http.assert_not_called()

    def test_each_host_prohibition_prevents_background_refresh_thread(self):
        for flag in FLAGS:
            with self.subTest(flag=flag):
                settings = {name: "0" for name in FLAGS}
                settings[flag] = "1"
                with patch.dict(os.environ, settings), patch.object(shared_records.threading, "Thread") as thread:
                    shared_records.start_refresh()
                thread.assert_not_called()

    def test_public_refresh_without_prohibition_still_attempts_transport(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {name: "0" for name in FLAGS}):
            fetch = Mock(side_effect=OSError("Controlled unavailable publisher"))
            self.assertFalse(shared_records.refresh(Path(temporary) / "records", fetch))
            self.assertEqual(1, fetch.call_count)
            self.assertEqual(shared_records.BASE + "cache.json", fetch.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
