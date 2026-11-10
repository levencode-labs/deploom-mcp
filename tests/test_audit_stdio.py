from __future__ import annotations

import asyncio
import json
import tempfile
import os
import sys
import unittest
from pathlib import Path
import deploom_mcp

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class AuditStdioTests(unittest.TestCase):
    def test_extended_server_exposes_both_tools_and_rejects_invalid_audit(self):
        async def scenario():
            env = os.environ.copy()
            # Test the package imported by this runner, not a different globally
            # installed wheel selected by a fresh child's default sys.path.
            env["PYTHONPATH"] = str(Path(deploom_mcp.__file__).resolve().parent.parent)
            parameters = StdioServerParameters(command=sys.executable,
                args=["-m", "deploom_mcp.audit_server"], env=env)
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual({
                        "get_upgrade_plan", "evaluate_assignment", "explain_conflict", "audit_dependencies", "get_project_capabilities",
                        "get_goal_plan", "get_goal_report", "verify_goal_candidate", "apply_goal_candidate",
                        "get_goal_checkpoint", "observe_workspace_inventory_v1", "plan_workspace_snapshot_v1",
                        "get_workspace_plan_details", "model_workspace_upgrade_v1",
                        "review_workspace_transitive_overrides_v1",
                    }, {tool.name for tool in tools.tools})
                    result = await session.call_tool("audit_dependencies", {"project_path": "relative"})
                    self.assertTrue(result.is_error)
                    self.assertEqual("invalid-project", result.structured_content["error"]["code"])
                    self.assertEqual("UNKNOWN", result.structured_content["security"]["verdict"])
        asyncio.run(scenario())

    def test_partial_workspace_contract_through_real_stdio(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            manifest = {"name": "fixture", "version": "1.0.0", "workspaces": ["packages/*"]}
            member = {"name": "app", "version": "1.0.0", "dependencies": {"demo": "1.0.0"}}
            (root / "packages/app").mkdir(parents=True)
            (root / "package.json").write_text(json.dumps(manifest))
            (root / "packages/app/package.json").write_text(json.dumps(member))
            (root / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": {
                "": manifest, "packages/app": member, "node_modules/app": {"link": True, "resolved": "packages/app"},
                "node_modules/demo": {"version": "1.0.0"}}}))
            async def scenario():
                env = os.environ.copy()
                env["PYTHONPATH"] = str(Path(deploom_mcp.__file__).resolve().parent.parent)
                parameters = StdioServerParameters(command=sys.executable, args=["-m", "deploom_mcp.audit_server"], env=env)
                async with stdio_client(parameters) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        inventory = (await session.call_tool("observe_workspace_inventory_v1",
                                     {"project_path": str(root)})).structured_content["inventory"]
                        request = {"schemaVersion": 2, "goalKind": "TARGETED_UPGRADE",
                            "inventoryDigest": inventory["inventoryDigest"], "environment": {}, "topology": None,
                            "targets": [{"selector": {"owner": "packages/app", "section": "dependencies", "name": "demo"},
                                "approvedLines": [1], "versions": ["1.1.0", "1.0.0"], "exactTarget": None}],
                            "packuments": {"demo": {"versions": {
                                "1.0.0": {"name": "demo", "version": "1.0.0"},
                                "1.1.0": {"name": "demo", "version": "1.1.0", "peerDependencies": {"new-peer": "^1"},
                                    "peerDependenciesMeta": {"new-peer": {"optional": True}}}}}}}
                        async def plan():
                            response = await session.call_tool("get_upgrade_plan", {"project_path": str(root), "workspace_request": request})
                            return response.structured_content
                        partial = await plan()
                        self.assertTrue(partial["ok"], partial)
                        self.assertEqual(partial["constraintModel"]["status"], "SAT_IN_MODELED_DOMAIN")
                        self.assertEqual(partial["constraintModel"]["domain"]["evidenceCoverage"], "PARTIAL")
                        details = partial["details"]
                        evidence = (await session.call_tool("get_workspace_plan_details", {
                            "plan_id": details["planId"], "request_id": partial["requestId"],
                            "candidate_id": partial["candidateId"], "snapshot_sha256": details["snapshotSha256"],
                            "owner": "packages/app", "section": "dependencies"})).structured_content
                        self.assertTrue(evidence["ok"], evidence)
                        request["targets"][0]["exactTarget"] = "1.1.0"
                        exact = await plan()
                        self.assertEqual(exact["constraintModel"]["status"], "UNKNOWN")
                        self.assertIsNone(exact["candidateId"])
                        request["schemaVersion"] = 99
                        self.assertEqual((await plan())["error"]["code"], "INVALID_WORKSPACE_REQUEST_VERSION")
            asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
