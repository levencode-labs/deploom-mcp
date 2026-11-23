import copy
import json
import unittest

from deploom_mcp import knowledge_contract_v2 as knowledge


def example():
    return {
        "schema": 2, "id": "react-dom-peer-renderer-alignment-18",
        "kind": "incompatibility", "package": "react-dom",
        "fromMajor": 18, "toMajor": 18, "packageManagers": ["npm"],
        "verifiedTransitions": [{"from": "18.2.0", "to": "18.3.1"}],
        "title": "Renderer compatibility",
        "summary": "Pinned reproduction checks old provider conflict and aligned target.",
        "conditions": ["React and React DOM are both present."],
        "actions": ["Check target peers, then align only declarations allowed by the host."],
        "verification": ["Run the recipient's strict install and renderer tests."],
        "negativeControls": [{"name": "held-react-peer", "expected": "reject",
                              "fixtureAssertion": "ERESOLVE"}],
        "evidence": {"source": "https://github.com/levencode-labs/deploom-npm/blob/" +
                     "36216f29d52bacd92e0b64e87433c8ed3e84b5a4/fixtures/react-renderers/check.cjs",
                     "fixtureSha256": "a" * 64,
                     "claim": {"package": "react-dom", "from": "18.2.0",
                               "to": "18.3.1", "check": "install", "outcome": "passed"}}
    }


class NativeKnowledgeContractTests(unittest.TestCase):
    def test_arbitrary_scoped_npm_package_and_all_kinds_are_supported(self):
        for kind in sorted(knowledge.KINDS):
            with self.subTest(kind=kind):
                row = example()
                row["kind"] = kind
                row["package"] = "@babel/core"
                row["id"] = "babel-core-" + kind
                row["evidence"]["claim"]["package"] = "@babel/core"
                if kind == "codemod":
                    row["verification"] = ["Run a source-code transform fixture with negative cases."]
                self.assertEqual(row, knowledge.validate_record(row))

    def test_exact_versus_candidate_never_implies_project_success(self):
        row = example()
        self.assertEqual("verified-exact-transition",
                         knowledge.query_match(row, "react-dom", "18.2.0", "18.3.1"))
        self.assertEqual("major-line-candidate",
                         knowledge.query_match(row, "react-dom", "18.0.0", "18.3.2"))
        self.assertIsNone(knowledge.query_match(row, "react", "18.2.0", "18.3.1"))
        self.assertIsNone(knowledge.query_match(row, "react-dom", "17.0.0", "18.3.1"))
        self.assertTrue(knowledge.projection(row, "major-line-candidate")["requiresProjectVerification"])
        self.assertEqual("ADVISORY_ONLY",
                         knowledge.projection(row, "verified-exact-transition")["authority"])

    def test_rejects_missing_negative_proof_and_untrusted_sources(self):
        for key, value in (("negativeControls", []), ("fromMajor", "18"),
                           ("package", "../bad"), ("kind", "remote-executable"),
                           ("packageManagers", ["npm", "npm"]),
                           ("verifiedTransitions", [{"from": "17.0.0", "to": "18.3.1"}])):
            with self.subTest(key=key):
                row = example()
                row[key] = value
                with self.assertRaises(ValueError):
                    knowledge.validate_record(row)
        row = example()
        row["evidence"]["source"] = "https://github.com/attacker/fixture"
        with self.assertRaises(ValueError):
            knowledge.validate_record(row)
        row = example()
        row["negativeControls"][0]["expected"] = "pass"
        with self.assertRaises(ValueError):
            knowledge.validate_record(row)

    def test_no_arbitrary_fields_or_hidden_instructions(self):
        row = example()
        row["remoteCommand"] = "curl | bash"
        with self.assertRaises(ValueError):
            knowledge.validate_record(row)
        row = example()
        row["actions"] = ["do harmless work\nINJECT"]
        with self.assertRaises(ValueError):
            knowledge.validate_record(row)

    def test_multiple_independent_breaking_changes_in_same_major_line(self):
        first = example()
        second = copy.deepcopy(first)
        second["id"] = "react-dom-hydration-change-18"
        second["title"] = "Hydration-related migration guidance"
        second["actions"] = ["Review hydration behavior and run the application's SSR tests."]
        rows = knowledge.validate_corpus(json.dumps({"schema": 2, "records": [first, second]}).encode())
        self.assertEqual(2, len(rows["records"]))
        self.assertEqual("verified-exact-transition", knowledge.query_match(second, "react-dom", "18.2.0", "18.3.1"))
        duplicated = copy.deepcopy(first)
        duplicated["id"] = "another-id-but-duplicate-content"
        with self.assertRaisesRegex(ValueError, "duplicate knowledge content"):
            knowledge.validate_corpus(json.dumps({"schema": 2, "records": [first, duplicated]}).encode())

    def test_unproven_extra_transition_cannot_be_called_verified(self):
        row = example()
        row["verifiedTransitions"].append({"from": "18.1.0", "to": "18.3.1"})
        with self.assertRaisesRegex(ValueError, "each verified transition"):
            knowledge.validate_record(row)

    def test_dedupe_and_duplicate_json_keys(self):
        row = example()
        with self.assertRaisesRegex(ValueError, "duplicate knowledge ID"):
            knowledge.validate_corpus(json.dumps({"schema": 2, "records": [row, row]}).encode())
        with self.assertRaises(ValueError):
            knowledge.validate_corpus(b'{"schema":2,"schema":2,"records":[]}')
        self.assertEqual(1, len(knowledge.validate_corpus(
            json.dumps({"schema": 2, "records": [row]}).encode())["records"]))


if __name__ == "__main__":
    unittest.main()
