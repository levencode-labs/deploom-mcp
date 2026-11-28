"""Native batch evidence: exact/major/negative controls with fixed signed bytes."""
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from deploom_mcp import shared_knowledge_batch as batch
from deploom_mcp import shared_records_v2 as signed
from deploom_mcp.knowledge_contract_v2 import validate_record


def compact(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def query(name="react-dom", from_version="18.2.0", to_version="18.3.1", manager="npm"):
    return {"package": name, "from_version": from_version,
            "to_version": to_version, "package_manager": manager}


class SignedBatchKnowledgeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.key = Ed25519PrivateKey.generate()
        public = self.key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
        self.pin = self.root / "pub.hex"
        self.pin.write_text(public.hex())
        original = signed.public_key_file
        signed.public_key_file = self.pin
        self.addCleanup(setattr, signed, "public_key_file", original)
        self.records = [self._record()]
        self.revision = 29
        self._publish()

    def _record(self):
        result = {
            "schema": 2, "id": "react-dom-peer-alignment-v18",
            "kind": "incompatibility", "package": "react-dom",
            "fromMajor": 18, "toMajor": 18, "packageManagers": ["npm"],
            "verifiedTransitions": [{"from": "18.2.0", "to": "18.3.1"}],
            "title": "Renderer requires matching peer ranges",
            "summary": "An independently tested renderer peer constraint.",
            "conditions": ["React and its DOM renderer are both declared."],
            "actions": ["Inspect the actual target peerDependencies before proposing an edit."],
            "verification": ["Re-run strict install and renderer tests."],
            "negativeControls": [{"name": "held-react-conflict", "expected": "reject",
                                  "fixtureAssertion": "ERESOLVE"}],
            "evidence": {
                "source": "https://github.com/levencode-labs/deploom-npm/blob/" +
                          "b79d3291010f068ebb87a3ecbcd72a8ab4f67130/fixtures/react-renderers/check.cjs",
                "fixtureSha256": "6c4f9d031d1d9201c353e44845bf77e6b3de6ad52c4de9697bda0d527ea55970",
                "claim": {"package": "react-dom", "from": "18.2.0", "to": "18.3.1",
                          "check": "install", "outcome": "passed"},
            },
        }
        validate_record(result)
        return result

    def _publish(self):
        self.revision += 1
        buckets = {}
        self.resources = {}
        for record in self.records:
            prefix = hashlib.sha256(record["package"].encode()).hexdigest()[:2]
            buckets.setdefault(prefix, []).append(record)
        index = {"schema": 2, "revision": self.revision, "sourceSha256": "a" * 64,
                 "recordCount": len(self.records), "buckets": {}}
        for bucket, records in buckets.items():
            blob = compact({"schema": 2, "records": records})
            sha = hashlib.sha256(blob).hexdigest()
            name = "shards/" + bucket + "/" + sha + ".json"
            index["buckets"][bucket] = [{
                "path": name, "sha256": sha, "bytes": len(blob), "records": len(records)}]
            self.resources[signed.BASE + name] = blob
        raw = compact(index)
        sig = self.key.sign(signed.DOMAIN + hashlib.sha256(raw).digest())
        self.resources[signed.BASE + "index.json"] = raw
        self.resources[signed.BASE + "index.sig"] = sig

        def fetch(url, limit, etag=None):
            self.assertIn(url, self.resources)
            self.assertLessEqual(len(self.resources[url]), limit)
            return self.resources[url], None

        self.assertTrue(signed.refresh(self.root, fetch))

    def _private_snapshot(self, *, records=None, domain=None):
        directory = self.root.resolve() / "private"
        directory.mkdir(exist_ok=True)
        repository = "https://git.example.invalid/team/knowledge/-/blob/"
        key = Ed25519PrivateKey.generate()
        if records is None:
            record = self._record()
            record["package"] = record["evidence"]["claim"]["package"] = "@org/widget"
            record["evidence"]["source"] = repository + "b" * 40 + "/fixtures/widget/check.cjs"
            records = [record]
        raw = compact({"schema": 2, "records": records})
        digest = hashlib.sha256(raw)
        (directory / "records.json").write_bytes(raw)
        (directory / "records.sig").write_bytes(
            key.sign((domain or batch.private.DOMAIN) + digest.digest()))
        return records, {
            "DEPLOOM_MCP_PRIVATE_SCOPES": "@org",
            "DEPLOOM_MCP_RECORDS_MIRROR_ONLY": "1",
            "DEPLOOM_MCP_LOCAL_CACHE_ONLY": "1",
            "DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE": "1",
            "DEPLOOM_MCP_KNOWLEDGE_DISABLED": "0",
            "DEPLOOM_MCP_PRIVATE_KNOWLEDGE_DIR": str(directory),
            "DEPLOOM_MCP_PRIVATE_KNOWLEDGE_SHA256": digest.hexdigest(),
            "DEPLOOM_MCP_PRIVATE_KNOWLEDGE_PUBLIC_KEY": key.public_key().public_bytes_raw().hex(),
            "DEPLOOM_MCP_PRIVATE_KNOWLEDGE_SOURCE": repository,
        }

    def test_private_snapshot_batch_uses_existing_matcher_and_keeps_sources_separate(self):
        _, settings = self._private_snapshot()
        requests = [query("@org/widget"), query(), query("@org/widget", "18.1.0", "18.3.1"),
                    query("@org/widget"), query("@org/missing"), query("@org/widget", manager="yarn")]
        with patch.dict("os.environ", settings), patch("socket.getaddrinfo", side_effect=AssertionError("Offline")):
            result = batch.lookup_batch(self.root, requests)
            only_private = batch.lookup_batch(None, [requests[0]])
        self.assertEqual(result["snapshotSource"], "verified-pages")
        self.assertEqual(["found", "found", "found", "duplicate-query", "no-matching-record", "no-matching-record"],
                         [row["status"] for row in result["results"]])
        exact, public, candidate, duplicate, _, _ = result["results"]
        self.assertEqual(duplicate["duplicateOf"], 0)
        self.assertEqual(exact["snapshotSource"], "verified-private-local")
        self.assertEqual(exact["snapshotSha256"], settings["DEPLOOM_MCP_PRIVATE_KNOWLEDGE_SHA256"])
        self.assertEqual(exact["knowledge"][0]["matchStrength"], "verified-exact-transition")
        self.assertEqual(exact["knowledge"][0]["sourceCommit"], "b" * 40)
        self.assertTrue(exact["knowledge"][0]["requiresProjectVerification"])
        self.assertEqual(candidate["knowledge"][0]["matchStrength"], "major-line-candidate")
        self.assertEqual(public["knowledge"][0]["evidence"]["claim"]["package"], "react-dom")
        self.assertNotIn("snapshotSha256", public)
        self.assertEqual(only_private["status"], "ready")
        self.assertEqual(only_private["snapshotSource"], "verified-private-local")

    def test_private_snapshot_plan_preserves_provenance_and_disables_official_lookup(self):
        _, settings = self._private_snapshot()
        from deploom_mcp.workspace_impact import build_project_impact

        project = self.root / "project"
        project.mkdir()
        target = {"declaration": [".", "dependencies", "@org/widget"],
                  "baselineLock": ["unused", "18.2.0"], "selectedVersion": "18.3.1"}
        with patch.dict("os.environ", settings), patch(
            "deploom_mcp.workspace_impact.knowledge_directory", return_value=self.root,
        ), patch("deploom_mcp.workspace_impact.collect_workspace_releases",
                 side_effect=AssertionError("Private package cannot use public evidence")):
            impact = build_project_impact(project, [target], ["."], "c" * 64,
                                          registry="https://registry.npmjs.org", evidence_mode="ON_DEMAND")
        signed_evidence = impact["transitions"][0]["evidence"]["signedKnowledge"]
        self.assertEqual(signed_evidence["snapshotSource"], "verified-private-local")
        self.assertEqual(signed_evidence["snapshotSha256"], settings["DEPLOOM_MCP_PRIVATE_KNOWLEDGE_SHA256"])
        self.assertIsNone(signed_evidence["snapshotRevision"])
        self.assertEqual(signed_evidence["records"][0]["recipientApplicability"], "UNKNOWN")
        self.assertEqual(impact["evidenceMetrics"]["httpTransactions"], 0)

    def test_private_snapshot_failures_preserve_public_knowledge(self):
        records, settings = self._private_snapshot()
        requests = [query("@org/widget"), query()]
        bad_configs = (
            {"DEPLOOM_MCP_PRIVATE_KNOWLEDGE_SHA256": "a" * 64},
            {"DEPLOOM_MCP_PRIVATE_KNOWLEDGE_PUBLIC_KEY": "a" * 64},
            {"DEPLOOM_MCP_PRIVATE_KNOWLEDGE_SOURCE": "https://other.example.invalid/team/knowledge/-/blob/"},
            {"DEPLOOM_MCP_PRIVATE_KNOWLEDGE_DIR": "private-from-project"},
        )
        for overrides in bad_configs:
            with self.subTest(overrides=overrides), patch.dict("os.environ", settings | overrides):
                result = batch.lookup_batch(self.root, requests)
                self.assertEqual(["private-snapshot-unavailable", "found"], [row["status"] for row in result["results"]])

        foreign = copy.deepcopy(records[0])
        foreign["id"] = "unscoped-public-package"
        foreign["package"] = foreign["evidence"]["claim"]["package"] = "react-dom"
        _, public_domain = self._private_snapshot(records=records, domain=signed.DOMAIN)
        with patch.dict("os.environ", public_domain):
            result = batch.lookup_batch(self.root, requests)
            self.assertEqual(["private-snapshot-unavailable", "found"], [row["status"] for row in result["results"]])
        _, mixed = self._private_snapshot(records=records + [foreign])
        with patch.dict("os.environ", mixed):
            result = batch.lookup_batch(self.root, requests)
            self.assertEqual(["private-snapshot-unavailable", "found"], [row["status"] for row in result["results"]])

    def test_private_snapshot_requires_operator_opt_in_and_respects_disabled_mode(self):
        _, settings = self._private_snapshot()
        requests = [query("@org/widget"), query()]
        with patch.dict("os.environ", settings | {
            "DEPLOOM_MCP_RECORDS_MIRROR_ONLY": "0", "DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE": "0",
        }), patch.object(batch.private, "_read_bytes", side_effect=AssertionError("File access forbidden")):
            response = batch.lookup_batch(self.root, requests)
            self.assertEqual(["skipped-private-scope", "found"], [row["status"] for row in response["results"]])
        with patch.dict("os.environ", settings | {"DEPLOOM_MCP_KNOWLEDGE_DISABLED": "1"}), patch.object(
            batch.private, "load_snapshot", side_effect=AssertionError("Disabled"),
        ):
            response = batch.lookup_batch(self.root, requests)
            self.assertEqual("disabled-by-policy", response["status"])
            self.assertTrue(all(not row["knowledge"] for row in response["results"]))

    def test_exact_major_miss_duplicate_and_all_local(self):
        with patch.object(batch.signed, "get", side_effect=AssertionError("HTTP forbidden")):
            found = batch.lookup_batch(self.root, [
                query(), query("react-dom", "18.1.0", "18.3.2"),
                query("react-dom", "17.0.0", "18.3.1"),
                query(), query("react", "18.2.0", "18.3.1"), query("react-dom", "17.0.0", "18.0.0")])
        self.assertTrue(found["ok"], found)
        self.assertEqual("ADVISORY_ONLY", found["authority"])
        self.assertEqual("verified-pages", found["snapshotSource"])
        self.assertEqual(2, found["snapshotSchema"])
        self.assertEqual(["found", "found", "found", "duplicate-query",
                          "no-matching-record", "no-matching-record"], [r["status"] for r in found["results"]])
        self.assertEqual("intermediate-transition-candidate", found["results"][2]["knowledge"][0]["matchStrength"])
        self.assertTrue(found["results"][2]["knowledge"][0]["requiresProjectVerification"])
        self.assertEqual(0, found["results"][3]["duplicateOf"])
        exact = found["results"][0]["knowledge"][0]
        self.assertEqual("verified-exact-transition", exact["matchStrength"])
        self.assertEqual("incompatibility", exact["kind"])
        self.assertEqual("react-dom-peer-alignment-v18", exact["recordId"])
        self.assertEqual("b79d3291010f068ebb87a3ecbcd72a8ab4f67130", exact["sourceCommit"])
        self.assertEqual("reject", exact["negativeControls"][0]["expected"])
        self.assertTrue(exact["requiresProjectVerification"])
        self.assertEqual("major-line-candidate",
                         found["results"][1]["knowledge"][0]["matchStrength"])
        self.assertLessEqual(found["truncation"]["bytes"], batch.MAX_OUTPUT_BYTES)

    def test_private_scopes_and_disabled_evidence_do_not_look_up(self):
        with patch.dict("os.environ", {"DEPLOOM_MCP_PRIVATE_SCOPES": "@org,company"}):
            response = batch.lookup_batch(self.root, [
                query("@org/private", "1.0.0", "2.0.0"),
                query("@company/foo", "1.0.0", "2.0.0"), query()])
        self.assertEqual(["skipped-private-scope", "skipped-private-scope", "found"],
                         [r["status"] for r in response["results"]])
        for env in ("DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE", "DEPLOOM_MCP_KNOWLEDGE_DISABLED"):
            with self.subTest(flag=env), patch.dict("os.environ", {env: "1"}):
                response = batch.lookup_batch(self.root, [query()])
                self.assertEqual("disabled-by-policy", response["status"])
                self.assertEqual([], response["results"][0]["knowledge"])
                self.assertEqual("none", response["snapshotSource"])
        with patch.dict("os.environ", {"DEPLOOM_MCP_LOCAL_CACHE_ONLY": "1",
                                      "DEPLOOM_MCP_MIGRATION_OFFLINE": "1"}):
            response = batch.lookup_batch(self.root, [query()])
            self.assertEqual("found", response["results"][0]["status"])

    def test_stdio_startup_keeps_verified_local_knowledge_and_explicit_denial(self):
        import os
        import sys
        from deploom_mcp import server, shared_records

        for explicit, status, source in (("0", "found", "verified-pages"),
                                         ("1", "disabled-by-policy", "none")):
            with self.subTest(explicit=explicit):
                def serve(*, transport):
                    self.assertEqual("stdio", transport)
                    self.assertEqual("1", os.environ.get("DEPLOOM_MCP_MIGRATION_OFFLINE"))
                    reply = batch.lookup_batch(self.root, [query()])
                    self.assertEqual(status, reply["results"][0]["status"])
                    self.assertEqual(source, reply["snapshotSource"])
                    single = shared_records.lookup(self.root, "react-dom", "18.2.0", "18.3.1")
                    self.assertEqual(status, single["status"])
                    self.assertEqual(source, single["snapshotSource"])

                with (patch.dict("os.environ", {"DEPLOOM_MCP_LOCAL_CACHE_ONLY": "1",
                                                "DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE": explicit,
                                                "DEPLOOM_MCP_KNOWLEDGE_DISABLED": "0"}),
                      patch.object(sys, "argv", ["deploom-mcp"]),
                      patch.object(server.server, "run", side_effect=serve) as run,
                      patch("deploom_mcp.shared_records.threading.Thread",
                            side_effect=AssertionError("Cache refresh not allowed in local-only mode"))):
                    self.assertEqual(0, server.main())
                    run.assert_called_once_with(transport="stdio")

    def test_local_only_preserves_the_approved_registry_and_audit_guards(self):
        import asyncio
        from deploom_mcp.dependency_audit import AuditError, audit_dependencies
        from deploom_mcp.planning_registry import planning_registry, private_metadata_registry

        approved = "https://packages.example.test/npm"
        with patch.dict("os.environ", {"DEPLOOM_MCP_LOCAL_CACHE_ONLY": "1",
                                       "DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE": "0",
                                       "DEPLOOM_MCP_ALLOWED_PROJECT_REGISTRY": approved}):
            self.assertEqual(approved, private_metadata_registry(approved))
            self.assertEqual(approved, planning_registry(self.root, approved))
            with self.assertRaises(AuditError) as error:
                planning_registry(self.root, "https://registry.npmjs.org")
            self.assertEqual("project-registry-not-authorized", error.exception.code)
            audit = asyncio.run(audit_dependencies(project_path=str(self.root / "missing")))
            self.assertEqual("standalone-audit-external-evidence-disabled", audit["error"]["code"])

    def test_invalid_batch_is_error_not_silent_fallback(self):
        invalid = [
            [], [query()] * 33, "not-a-list",
            [dict(query(), injected="true")],
            [query("../path", "1.0.0", "2.0.0")],
            [query("react-dom", "1.x", "2.0.0")],
            [query("react-dom", "1.0.0", "1.0.0")],
            [query("react-dom", "1.0.0", "2.0.0", "bogus")],
            [{"package": 42, "from_version": "1.0.0", "to_version": "2.0.0",
              "package_manager": "npm"}],
        ]
        for case in invalid:
            with self.subTest(value=repr(case)[:120]):
                response = batch.lookup_batch(self.root, case)
                self.assertFalse(response["ok"])
                self.assertEqual("INVALID_KNOWLEDGE_BATCH", response["error"]["code"])

    def test_unavailable_corrupt_local_shard_fails_closed(self):
        missing = batch.lookup_batch(self.root / "no-database", [query()])
        self.assertEqual("snapshot-unavailable", missing["status"])
        self.assertNotEqual("verified-pages", missing["snapshotSource"])
        from contextlib import closing
        # Windows cannot remove the temporary SQLite file while a connection
        # remains open: contextmanager commit/rollback alone does not close it.
        with closing(sqlite3.connect(self.root / signed.DB)) as db, db:
            db.execute("UPDATE shard SET data=?", (b"{}",))
        invalid = batch.lookup_batch(self.root, [query(), query("react", "18.2.0", "18.3.1")])
        self.assertEqual("snapshot-unavailable", invalid["status"])
        self.assertTrue(all(not r["knowledge"] for r in invalid["results"]))

    def test_large_hit_count_is_disclosed_never_silent(self):
        cards = []
        for n in range(16):
            item = copy.deepcopy(self._record())
            item["id"] = "renderer-change-" + str(n).zfill(3)
            item["title"] = "Renderer change " + str(n)
            cards.append(item)
        self.records = cards
        self._publish()
        result = batch.lookup_batch(self.root, [query()])
        self.assertEqual("found", result["results"][0]["status"])
        self.assertEqual(16, result["results"][0]["matchingCount"])
        self.assertEqual(batch.MAX_CARDS_PER_QUERY, len(result["results"][0]["knowledge"]))
        self.assertTrue(result["truncation"]["truncated"])
        self.assertEqual(16 - batch.MAX_CARDS_PER_QUERY, result["truncation"]["omittedRecords"])

    def test_full_output_respects_bytes_and_sorting_with_multiple_queries(self):
        cards = []
        for n in range(8):
            item = copy.deepcopy(self._record())
            item["id"] = "check-" + str(n).zfill(4)
            item["title"] = "Issue " + str(n)
            item["summary"] = "A" * 1000
            item["actions"] = ["B" * 400] * 1
            cards.append(item)
        self.records = cards
        self._publish()
        requests = [query("react-dom", "18.1." + str(n + 1), "18.3.1")
                    for n in range(30)]
        out = batch.lookup_batch(self.root, requests)
        self.assertTrue(out["ok"], out)
        self.assertLessEqual(out["truncation"]["bytes"], batch.MAX_OUTPUT_BYTES)
        self.assertTrue(out["truncation"]["truncated"])
        self.assertEqual(30, len(out["results"]))
        self.assertTrue(any(r["omittedCount"] > 0 for r in out["results"]))
        self.assertTrue(all(r["matchingCount"] == 8 for r in out["results"]))


if __name__ == "__main__":
    unittest.main()

