"""Current-version coverage in the flat planning tool (synthetic data only)."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import jsonschema
from test_registry_projection import SCHEMA, compact_blueprint, fixture, transcode
from deploom_mcp.blueprint import BlueprintOptions, BlueprintPolicy, _exec_env


class CurrentSecurityProjectionTests(unittest.TestCase):
    def test_sources_zero_unknown_and_legacy_remain_distinct_after_truncation(self):
        plan, manifest = fixture(diag=False)
        rows = plan["proposals"][0]["rows"]
        seed = rows.pop()
        results = [
            {"status": "ASSESSED", "source": "OFFLINE_OSV_NPM", "findingCount": 0},
            {"status": "ASSESSED", "source": "REGISTRY_BULK_ADVISORY", "findingCount": 1,
             "osvReason": "PUBLIC_PACKAGE_UNCONFIRMED", "registryReason": "REGISTRY_EXACT_VERSION"},
            {"status": "UNKNOWN", "reason": "MISSING_SEVERITY", "osvReason": "MISSING_SEVERITY",
             "registryReason": "COVERAGE_UNCONFIRMED"},
            {"status": "UNKNOWN", "reason": "NON_REGISTRY_SPEC"},
            None,
        ]
        for index, result in enumerate(results):
            row = dict(seed, package=f"example-{index}")
            if result is not None:
                row["currentSecurityEvidence"] = dict(result, raw="unpublished provider body")
            rows.append(row)
        plan["counts"]["total"] = len(rows)
        bp = transcode(plan, manifest)
        jsonschema.validate(bp, SCHEMA)
        for row, expected in zip(bp["dependencies"], results):
            self.assertEqual(row["evidence"].get("currentSecurityEvidence"), expected)
        # Compact responses may also be produced from a stored blueprint.
        bp["dependencies"][0]["evidence"]["currentSecurityEvidence"]["extra"] = "unexpected message"
        compact = compact_blueprint(bp, max_inline_rows=1)
        self.assertEqual(len(compact["dependencies"]), 1)
        self.assertEqual(compact["dependencies"][0]["evidence"]["currentSecurityEvidence"], results[0])
        self.assertNotIn("unpublished provider body", json.dumps(bp))
        expected = {"totalRows": 5, "unreportedRows": 1,
                    "assessedBySource": {"OFFLINE_OSV_NPM": 1, "REGISTRY_BULK_ADVISORY": 1},
                    "unknownByReason": {"MISSING_SEVERITY": 1, "NON_REGISTRY_SPEC": 1}}
        self.assertEqual(bp["summary"]["currentSecurityCoverage"], expected)
        self.assertEqual(compact["summary"]["currentSecurityCoverage"], expected)

    def test_missing_or_invalid_row_does_not_create_assessment(self):
        plan, manifest = fixture(diag=False)
        row = plan["proposals"][0]["rows"][0]
        for value in (None, {"status": "ASSESSED", "source": "OFFLINE_OSV_NPM", "findingCount": False},
                      {"status": "UNKNOWN", "reason": {}}, {"status": "ASSESSED", "source": "OFFLINE_OSV_NPM"}):
            with self.subTest(value=value):
                row["currentSecurityEvidence"] = value
                bp = transcode(plan, manifest)
                self.assertNotIn("currentSecurityEvidence", bp["dependencies"][0]["evidence"])
                self.assertNotIn("currentSecurityCoverage", bp["summary"])
                self.assertNotIn("currentSecurityCoverage", compact_blueprint(bp)["summary"])

    def test_offline_child_uses_host_identity_policy_and_explicit_corporate_destination(self):
        registry = "https://mirror.example.invalid/npm"
        base = {"DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE": "1", "DEPLOOM_MCP_OSV_DIR": "/cache/osv",
                "DEPLOOM_MCP_PUBLIC_NAMES": "one,@two/three", "DEPLOOM_MCP_PRIVATE_SCOPES": "@private",
                "DEPLOOM_MCP_REGISTRY_ADVISORY_SOURCE": registry, "DEPLOOM_MCP_CORPORATE_ADVISORY_SOURCE": registry,
                "DEPLOOM_DRAFT_CORPORATE_ADVISORY_SOURCE": "https://caller.example.invalid"}
        def child(base, selected):
            with patch.dict(os.environ, base, clear=True):
                return _exec_env(BlueprintPolicy(), "run", "ws", "project", BlueprintOptions(registry=selected))
        env = child(base, registry + "/")
        self.assertEqual(env["DEPLOOM_DRAFT_PUBLIC_NAMES"], "one,@two/three")
        self.assertEqual(env["DEPLOOM_DRAFT_PRIVATE_SCOPES"], "@private")
        self.assertEqual(env["DEPLOOM_DRAFT_CORPORATE_ADVISORY_SOURCE"], registry)
        for key in ("DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE", "DEPLOOM_MCP_OSV_DIR",
                    "DEPLOOM_MCP_REGISTRY_ADVISORY_SOURCE", "DEPLOOM_MCP_CORPORATE_ADVISORY_SOURCE"):
            with self.subTest(missing=key):
                env = child({k: v for k, v in base.items() if k != key}, registry)
                self.assertNotIn("DEPLOOM_DRAFT_CORPORATE_ADVISORY_SOURCE", env)
        self.assertNotIn("DEPLOOM_DRAFT_CORPORATE_ADVISORY_SOURCE", child(base, registry + "-other"))
        for public in ("https://REGISTRY.NPMJS.ORG.:443/path", "https://registry.yarnpkg.com",
                       "https://registry.npmjs.com", "https://registry。npmjs.org", "http://mirror.example.invalid"):
            env = child(dict(base, DEPLOOM_MCP_REGISTRY_ADVISORY_SOURCE=public,
                             DEPLOOM_MCP_CORPORATE_ADVISORY_SOURCE=public), public)
            self.assertNotIn("DEPLOOM_DRAFT_CORPORATE_ADVISORY_SOURCE", env)

    def test_private_packument_preflight_runs_before_child_and_has_separate_metadata_consent(self):
        from deploom_mcp.planning_registry import planning_registry
        from deploom_mcp.server import get_upgrade_plan

        corporate = "https://registry.example.invalid/group"
        base = {"DEPLOOM_MCP_PRIVATE_SCOPES": "@inside", "DEPLOOM_MCP_OSV_DIR": "/test/osv",
                "DEPLOOM_DRAFT_PRIVATE_METADATA_REGISTRY": corporate}
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            manifest = project / "package.json"
            manifest.write_text(json.dumps({"devDependencies": {"@inside/a": "^1.0.0"}}))
            without_cache = {k: v for k, v in base.items() if k != "DEPLOOM_MCP_OSV_DIR"}
            for env in (base, without_cache, dict(base, DEPLOOM_MCP_ALLOWED_PROJECT_REGISTRY=corporate),
                        dict(base, DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE="1"),
                        dict(base, DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE="1",
                             DEPLOOM_MCP_ALLOWED_PROJECT_REGISTRY="https://REGISTRY.NPMJS.ORG.:443/repo")):
                with self.subTest(env=env), patch.dict(os.environ, env, clear=True), patch(
                    "deploom_mcp.server.generate_upgrade_blueprint"
                ) as child:
                    self.assertEqual(get_upgrade_plan(project_path=str(project))["error"]["code"],
                                     "private-metadata-registry-required")
                    child.assert_not_called()
                    opts = BlueprintOptions(registry=corporate)
                    child_env = _exec_env(BlueprintPolicy(), "r", "ws", "p", opts)
                    self.assertEqual("@inside", child_env["DEPLOOM_DRAFT_PRIVATE_SCOPES"])
                    self.assertNotIn("DEPLOOM_DRAFT_PRIVATE_METADATA_REGISTRY", child_env)
            with patch.dict(os.environ, dict(base, DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE="1",
                                             DEPLOOM_MCP_ALLOWED_PROJECT_REGISTRY=corporate), clear=True):
                route = planning_registry(project, None)
                self.assertEqual(route, corporate)
                env = _exec_env(BlueprintPolicy(), "r", "ws", "p", BlueprintOptions(registry=route))
                self.assertEqual(corporate, env["DEPLOOM_DRAFT_PRIVATE_METADATA_REGISTRY"])
                self.assertNotIn("DEPLOOM_DRAFT_REGISTRY_ADVISORY_SOURCE", env)
            with patch.dict(os.environ, base, clear=True):
                manifest.write_text(json.dumps({"dependencies": {"outside": "1.0.0", "@inside/local": "file:fixture"}}))
                self.assertIsNone(planning_registry(project, None))
