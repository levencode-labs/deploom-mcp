"""Source-pinned regression cases for useful, explicitly advisory knowledge selection."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from deploom_mcp import knowledge_contract_v2 as contract
from deploom_mcp import shared_knowledge_batch as batch
from deploom_mcp.migration_evidence import _app
from deploom_mcp.workspace_impact import build_project_impact
import test_shared_knowledge_batch as fixtures


def scope(level="source-pinned-metadata"):
    return {"schema": 1, "level": level, "retainedInputSha256": "a" * 64,
            "archiveStatus": "UNKNOWN", "archiveReason": "MISSING_ARCHIVE_EVIDENCE",
            "projectStatus": "UNKNOWN", "conditionsRequired": True}


class KnowledgeSelectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SignedBatchKnowledgeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        data = json.loads((Path(__file__).parent / "fixtures" / "knowledge_lookup_cases.json").read_text(encoding="utf-8"))
        self.fixture.records = data["records"]
        self.fixture._publish()

    def lookup(self, package, before, after, manager="npm"):
        with patch.object(batch.signed, "get", side_effect=AssertionError("Lookup must remain offline")):
            response = batch.lookup_batch(self.fixture.root, [fixtures.query(package, before, after, manager)])
        self.assertTrue(response["ok"], response)
        return response["results"][0]

    def impact(self, package, before, after):
        project = self.fixture.root / "project"
        project.mkdir(exist_ok=True)
        target = {"declaration": [".", "dependencies", package], "baselineLock": ["unused", before], "selectedVersion": after}
        with (patch("deploom_mcp.workspace_impact.knowledge_directory", return_value=self.fixture.root),
              patch("deploom_mcp.workspace_impact.configured_state_root", return_value=self.fixture.root / "state"),
              patch("deploom_mcp.workspace_impact.inspect_usage", return_value=_app(reason="USAGE_NODE_UNAVAILABLE"))):
            return build_project_impact(project, [target], ["."], "a" * 64)["transitions"][0]

    def test_neighboring_typescript_query_selects_crossed_observed_boundary(self):
        row = self.lookup("typescript", "5.8.2", "5.9.3")
        self.assertEqual(["typescript59-buffer-source"], [r["id"] for r in row["knowledge"]])
        self.assertEqual("major-line-candidate", row["knowledge"][0]["matchStrength"])
        self.assertTrue(row["knowledge"][0]["requiresProjectVerification"])
        exact = self.lookup("typescript", "5.8.3", "5.9.2")
        self.assertEqual("verified-exact-transition", exact["knowledge"][0]["matchStrength"])

    def test_overlap_alone_downgrade_and_prerelease_do_not_import_upgrade_advice(self):
        for before, after in (("5.8.2", "5.8.4"), ("5.9.3", "5.8.2"),
                              ("5.8.2", "5.9.3-beta.1"), ("5.9.2", "5.9.3")):
            with self.subTest(before=before, after=after):
                self.assertEqual([], self.lookup("typescript", before, after)["knowledge"])
        self.assertEqual([], self.lookup("typescript", "5.8.2", "5.9.3", "yarn")["knowledge"])

    def test_large_jsdom_transition_composes_distinct_advisory_changes(self):
        row = self.lookup("jsdom", "23.0.0", "31.0.0")
        titles = {r["title"] for r in row["knowledge"]}
        self.assertIn("Migrate jsdom VirtualConsole forwarding", titles)
        self.assertIn("Replace jsdom ResourceLoader instances", titles)
        self.assertIn("Adopt the new optional jsdom canvas backend", titles)
        self.assertEqual(len(titles), len(row["knowledge"]))
        self.assertGreater(row["matchingRecordCount"], row["matchingCount"])
        self.assertEqual(row["deduplicatedCount"], row["matchingRecordCount"] - row["matchingCount"])
        for card in row["knowledge"]:
            self.assertEqual("ADVISORY_ONLY", card["authority"])
            self.assertTrue(card["requiresProjectVerification"])
            expected = ("verified-exact-transition" if card["id"] == "batch001-jsdom-23.0.0-31.0.0"
                        else "intermediate-transition-candidate")
            self.assertEqual(expected, card["matchStrength"])

    def test_one_call_project_impact_keeps_distinct_changes_and_unknown_applicability(self):
        transition = self.impact("jsdom", "23.0.0", "31.0.0")
        evidence = transition["evidence"]["signedKnowledge"]
        titles = {r["title"] for r in evidence["records"]}
        self.assertIn("Migrate jsdom VirtualConsole forwarding", titles)
        self.assertIn("Replace jsdom ResourceLoader instances", titles)
        self.assertTrue(all(r["recipientApplicability"] == "UNKNOWN" for r in evidence["records"]))
        for record in evidence["records"]:
            checks = record["applicabilityChecks"]
            self.assertEqual({"status": "MATCHED", "observed": "npm"}, checks["packageManager"])
            self.assertEqual({"from": "23.0.0", "to": "31.0.0"}, checks["transition"]["requested"])
            self.assertEqual(["."], checks["ownership"]["owners"])
            self.assertEqual("UNKNOWN", checks["semanticConditions"]["status"])
            self.assertEqual("UNKNOWN", checks["runtime"]["status"])

    def test_evidence_scope_is_closed_and_cannot_assert_recipient_success(self):
        row = copy.deepcopy(self.fixture.records[0])
        row["evidenceScope"] = scope()
        contract.validate_record(row)
        projected = contract.projection(row, "verified-exact-transition")
        self.assertEqual(scope(), projected["evidenceScope"])
        for key, value in (("level", "project-verified"), ("projectStatus", "VERIFIED"),
                           ("conditionsRequired", False), ("archiveStatus", "VERIFIED")):
            with self.subTest(key=key):
                invalid = copy.deepcopy(row)
                invalid["evidenceScope"][key] = value
                with self.assertRaises(ValueError):
                    contract.validate_record(invalid)
        del row["evidenceScope"]
        self.assertEqual("UNKNOWN", contract.projection(row, "verified-exact-transition")["evidenceScope"]["level"])

    def test_evidence_scope_affects_ranking_and_is_preserved_in_plan(self):
        metadata = self.fixture._record()
        metadata.update(id="aaaa-metadata", title="Metadata comparison", evidenceScope=scope())
        behavior = copy.deepcopy(metadata)
        behavior.update(id="zzzz-behavior", title="Observed fixture behavior", evidenceScope=scope("fixture-behavior"))
        self.fixture.records = [metadata, behavior]
        self.fixture._publish()
        selected = self.lookup("react-dom", "18.2.0", "18.3.1")["knowledge"]
        self.assertEqual("zzzz-behavior", selected[0]["id"])
        evidence = self.impact("react-dom", "18.2.0", "18.3.1")["evidence"]["signedKnowledge"]
        self.assertEqual({"source-pinned-metadata", "fixture-behavior"}, {r["evidenceScope"]["level"] for r in evidence["records"]})
        self.assertTrue(all(r["evidenceScope"]["projectStatus"] == "UNKNOWN" for r in evidence["records"]))


if __name__ == "__main__":
    unittest.main()
