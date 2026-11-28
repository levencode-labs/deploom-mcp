"""The manual updater reports each independent cache without hiding partial failure."""
import contextlib
import io
import json
import os
import runpy
import unittest
import warnings
from unittest.mock import patch
from urllib.error import HTTPError

from deploom_mcp import public_cache as cache
from deploom_mcp import shared_records
from deploom_mcp import shared_records_v2 as signed
import test_public_cache as osv_fixtures
import test_shared_records_v2 as knowledge_fixtures


class _Response(io.BytesIO):
    def __init__(self, body, etag):
        super().__init__(body)
        self.headers = {"ETag": etag}


class CacheRefreshCliTests(unittest.TestCase):
    def setUp(self):
        self.osv = osv_fixtures.PublicCacheTests()
        self.osv.setUp()
        self.addCleanup(self.osv.doCleanups)
        self.knowledge = knowledge_fixtures.SharedKnowledgeV2Tests()
        self.knowledge.setUp()
        self.addCleanup(self.knowledge.doCleanups)
        self.osv_dir = self.osv.root / "profile"
        self.records_dir = self.osv.root / "records"

    def _open(self, request, timeout):
        url = request.full_url
        if url.startswith(signed.BASE):
            body, etag = self.knowledge.fetch(url, signed.TOTAL_LIMIT)
        else:
            body, etag = self.osv.fetch(url, 256_000_000, request.get_header("If-none-match"))
        if body is None:
            raise HTTPError(url, 304, "Not modified", {}, None)
        return _Response(body, etag)

    def _invoke(self, *, now=None, legacy_ok=True):
        class Opener:
            open = staticmethod(self._open)

        output = io.StringIO()
        code, uncaught = 0, None
        settings = {
            "DEPLOOM_MCP_RECORDS_DIR": str(self.records_dir),
            "DEPLOOM_MCP_LOCAL_CACHE_ONLY": "0",
            "DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE": "0",
            "DEPLOOM_MCP_MIGRATION_OFFLINE": "0",
            "DEPLOOM_MCP_RECORDS_MIRROR_ONLY": "0",
            "DEPLOOM_MCP_KNOWLEDGE_DISABLED": "0",
        }
        clock = patch("time.time", return_value=now) if now is not None else contextlib.nullcontext()
        with (patch.dict(os.environ, settings),
              patch("sys.argv", ["deploom_mcp.public_cache", str(self.osv_dir)]),
              patch("urllib.request.build_opener", return_value=Opener()),
              patch.object(cache, "build_opener", return_value=Opener()),
              patch.object(shared_records, "refresh", return_value=legacy_ok) as legacy,
              contextlib.redirect_stdout(output), clock, warnings.catch_warnings()):
            warnings.simplefilter("ignore", RuntimeWarning)
            try:
                runpy.run_module("deploom_mcp.public_cache", run_name="__main__")
            except SystemExit as exc:
                code = exc.code
            except (ValueError, OSError) as exc:
                code, uncaught = 2, type(exc).__name__
        return code, output.getvalue(), uncaught, legacy.call_count

    def _result(self, observed, expected_code, expected_status):
        code, output, uncaught, legacy_calls = observed
        self.assertIsNone(uncaught, "CLI must report a structured result instead of aborting the remaining sources")
        self.assertEqual(code, expected_code)
        result = json.loads(output)
        self.assertEqual(result["schemaVersion"], 1)
        self.assertEqual(result["status"], expected_status)
        self.assertEqual(set(result["sources"]), {"osv", "knowledgeV2", "knowledgeV1"})
        self.assertEqual(legacy_calls, 1, "Legacy refresh must be attempted independently too")
        return result["sources"]

    def test_stale_osv_still_activates_signed_knowledge_and_preserves_osv_bytes(self):
        cache.refresh(self.osv_dir, self.osv.fetch)
        before = (self.osv_dir / "active.json").read_bytes()
        observed = self._invoke(now=self.osv.manifest["expires"] + 1)
        knowledge = signed.lookup(self.records_dir, "react-dom", "18.2.0", "18.3.1")
        self.assertIsNotNone(knowledge, "A stale OSV edition must not prevent an independent signed knowledge update")
        self.assertEqual(knowledge["knowledge"][0]["matchStrength"], "verified-exact-transition")
        self.assertEqual((self.osv_dir / "active.json").read_bytes(), before)
        self.assertTrue(cache.usable(self.osv_dir, json.loads(before)))
        sources = self._result(observed, 1, "PARTIAL")
        self.assertEqual(sources["osv"]["status"], "UNKNOWN")
        self.assertEqual(sources["osv"]["reason"], "SNAPSHOT_STALE")
        self.assertEqual(sources["knowledgeV2"]["status"], "READY")
        self.assertEqual(sources["knowledgeV1"]["status"], "READY")

    def test_bad_new_signed_shard_is_partial_and_preserves_previous_knowledge(self):
        self.assertTrue(signed.refresh(self.records_dir, self.knowledge.fetch))
        self.knowledge.index["revision"] += 1
        self.knowledge._update()
        self.knowledge.resources[signed.BASE + self.knowledge.path] = self.knowledge.shard[:-1]
        observed = self._invoke()
        sources = self._result(observed, 1, "PARTIAL")
        self.assertEqual(sources["osv"]["status"], "READY")
        self.assertEqual(sources["knowledgeV2"]["status"], "UNKNOWN")
        self.assertEqual(sources["knowledgeV2"]["reason"], "KNOWLEDGE_REFRESH_FAILED")
        self.assertEqual(sources["knowledgeV1"]["status"], "READY")
        self.assertEqual(signed.lookup(self.records_dir, "react-dom", "18.2.0", "18.3.1")["status"], "found")

    def test_all_sources_ready_exit_zero(self):
        sources = self._result(self._invoke(), 0, "READY")
        self.assertTrue(all(row["status"] == "READY" for row in sources.values()))
        self.assertEqual(sources["osv"]["edition"], self.osv.manifest["tag"])

    def test_all_sources_fail_exit_two_without_false_success(self):
        self.knowledge.resources[signed.BASE + "index.sig"] = b"\0" * 64
        observed = self._invoke(now=self.osv.manifest["expires"] + 1, legacy_ok=False)
        sources = self._result(observed, 2, "FAILED")
        self.assertTrue(all(row["status"] == "UNKNOWN" for row in sources.values()))
        self.assertEqual(sources["osv"]["reason"], "SNAPSHOT_STALE")
        self.assertFalse((self.osv_dir / "active.json").exists())
        self.assertIsNone(signed.lookup(self.records_dir, "react-dom", "18.2.0", "18.3.1"))

    def test_legacy_failure_is_not_reported_as_complete_success(self):
        sources = self._result(self._invoke(legacy_ok=False), 1, "PARTIAL")
        self.assertEqual(sources["osv"]["status"], "READY")
        self.assertEqual(sources["knowledgeV2"]["status"], "READY")
        self.assertEqual(sources["knowledgeV1"]["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
