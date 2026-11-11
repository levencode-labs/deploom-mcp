import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

import test_workspace_inventory as fixture_module
from deploom_mcp.distribution import resolve_core_dir
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.workspace_edits import check_workspace_patch, plan_workspace_edits
from deploom_mcp.workspace_goal_request import validate_goal_request
from deploom_mcp.workspace_inventory import observe_npm_workspace
from deploom_mcp.workspace_plan_snapshot import plan_workspace_snapshot
from deploom_mcp.workspace_upgrade import get_workspace_upgrade_plan

REGISTRY = 'https://registry.example.invalid'


class WorkspaceGoalRequestTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.WorkspaceInventoryTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.lock['packages']['node_modules/typescript'] = {'version': '5.0.0'}
        self.fixture.save()
        self.root = self.fixture.root
        sys.path.insert(0, str(resolve_core_dir()))
        self.addCleanup(sys.path.pop, 0)
        self.request = {'schemaVersion': 3, 'goalKind': 'TARGETED_UPGRADE', 'registry': REGISTRY, 'scope': [
            {'selector': {'name': 'kleur', 'owner': 'packages/a'}, 'approvedLines': [3], 'exactTarget': '3.0.1'},
            {'selector': {'name': 'typescript'}, 'approvedLines': [5]}]}

    def run_plan(self, request=None, before_worker=None, metadata_source=None):
        from dependency_live_roadmap_generator import LiveDataClient
        def metadata(name):
            versions = [f'5.0.{i}' for i in range(40)] if name == 'typescript' else ['3.0.0', '3.0.1', '4.0.0']
            return {'versions': {v: {'name': name, 'version': v, 'dist': {
                'tarball': f'{REGISTRY}/{name}/-/{name}-{v}.tgz'}} for v in versions}}, True
        async def worker(project_path, snapshot, **kwargs):
            if before_worker:
                before_worker()
            try:
                plan = plan_workspace_snapshot(Path(project_path), snapshot, **kwargs)
                return json.loads(json.dumps({'ok': True, 'plan': plan}))
            except GoalContractError as exc:
                return {'ok': False, 'error': {'code': exc.code}}
        inventory = {'ok': True, 'inventory': observe_npm_workspace(self.root)}
        with patch('deploom_mcp.workspace_goal_request.observe_workspace_inventory_v1',
                   new=AsyncMock(return_value=inventory)), patch(
            'deploom_mcp.workspace_upgrade.plan_workspace_snapshot_v1', side_effect=worker), patch.dict(
            os.environ, {'DEPLOOM_WORKSPACE_POLICY_REGISTRY': REGISTRY}, clear=True), patch.object(
            LiveDataClient, '_fetch_npm_metadata_result', side_effect=metadata_source or metadata), patch(
            'workspace_npm_preflight.observe_workspace_peer_overrides', return_value={}):
            request = request or self.request
            planner = plan_workspace_edits if request.get("responseMode") == "EDITS" else get_workspace_upgrade_plan
            return asyncio.run(planner(str(self.root), request))

    def test_manifest_patch_applies_exact_target_and_refuses_drift(self):
        manifest = self.root / "packages/a/package.json"
        original = manifest.read_bytes()
        request = {**self.request, "responseMode": "EDITS"}
        with tempfile.TemporaryDirectory() as directory:
            with patch("deploom_mcp.workspace_edits.configured_state_root", return_value=Path(directory)):
                result = self.run_plan(request)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["changeCount"], 1)
            self.assertEqual(result["installation"]["status"], "NOT_RUN")
            self.assertFalse(result["executionReady"])
            self.assertEqual(manifest.read_bytes(), original)
            artifact = Path(result["patch"]["path"])
            check_workspace_patch(self.root, result["inventoryDigest"], artifact, result["patch"]["sha256"])
            with self.assertRaisesRegex(GoalContractError, "WORKSPACE_PATCH_DIGEST_MISMATCH"):
                check_workspace_patch(self.root, result["inventoryDigest"], artifact, "0" * 64)
            subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
            subprocess.run(["git", "apply", "--check", "--unidiff-zero", str(artifact)], cwd=self.root, check=True)
            subprocess.run(["git", "apply", "--unidiff-zero", str(artifact)], cwd=self.root, check=True)
            expected = json.loads(original)
            expected["dependencies"]["kleur"] = "3.0.1"
            self.assertEqual(json.loads(manifest.read_bytes()), expected)
            with self.assertRaises(GoalContractError):
                check_workspace_patch(self.root, result["inventoryDigest"], artifact, result["patch"]["sha256"])

    def test_compact_edits_reuses_manifest_patch_and_keeps_blocker_details(self):
        request = {**self.request, "responseMode": "EDITS"}
        with tempfile.TemporaryDirectory() as directory:
            with patch("deploom_mcp.workspace_edits.configured_state_root", return_value=Path(directory)):
                full = self.run_plan(request)
                compact = self.run_plan({**request, "compactEdits": True})
            self.assertTrue(compact["compactEdits"])
            for key in ("inventoryDigest", "action", "verification", "executionReady", "changeCount"):
                self.assertEqual(compact[key], full[key])
            self.assertEqual(compact["patch"]["sha256"], full["patch"]["sha256"])
            self.assertEqual(Path(compact["patch"]["path"]).read_bytes(), Path(full["patch"]["path"]).read_bytes())
            self.assertNotIn("details", compact)

        blocked = json.loads(json.dumps(request))
        blocked["scope"][0]["exactTarget"] = "3.0.9"
        full = self.run_plan(blocked)
        compact = self.run_plan({**blocked, "compactEdits": True})
        self.assertEqual(compact["constraintModel"], full["constraintModel"])
        self.assertEqual(compact["action"], full["action"])
        self.assertNotIn("compactEdits", compact)
        self.assertIsNone(compact["patch"])
        self.assertEqual(self.run_plan({**request, "compactEdits": "true"})["error"]["code"],
                         "INVALID_WORKSPACE_COMPACT_EDITS_OPTION")

    def test_edits_mode_does_not_emit_patch_for_unknown(self):
        request = json.loads(json.dumps(self.request))
        request["responseMode"] = "EDITS"
        request["scope"][0]["exactTarget"] = "3.0.9"
        result = self.run_plan(request)
        self.assertEqual(result["constraintModel"]["status"], "UNKNOWN")
        self.assertIsNone(result["patch"])

    def test_held_workspace_owner_peer_limits_root_provider(self):
        self.fixture.manifests['packages/b/package.json']['peerDependencies']['typescript'] = '>=5 <5.0.5'
        self.fixture.lock['packages']['packages/b']['peerDependencies']['typescript'] = '>=5 <5.0.5'
        self.fixture.save()

        def metadata(name):
            versions = ['5.0.0', '5.0.4', '5.0.5'] if name == 'typescript' else ['3.0.0', '3.0.1', '4.0.0']
            rows = {v: {'name': name, 'version': v, 'dist': {
                'tarball': f'{REGISTRY}/{name}/-/{name}-{v}.tgz'}} for v in versions}
            if name == 'kleur':
                rows['3.0.1']['peerDependencies'] = {'typescript': '>=5.0.4'}
            return {'versions': rows}, True

        result = self.run_plan(metadata_source=metadata)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['constraintModel']['status'], 'SAT_IN_MODELED_DOMAIN')
        versions = {row['declaration'][2]: row['selectedVersion'] for row in result['jointAssignment']['targets']}
        self.assertEqual(versions['typescript'], '5.0.4')
        self.assertEqual(result['coverage']['ownerPeerConstraintCount'], 1)
        self.assertEqual(result['coverage']['constraintDeclarationCount'], 3)

        def stale_manifest():
            path = self.root / 'packages/b/package.json'
            manifest = json.loads(path.read_text(encoding='utf-8'))
            manifest['peerDependencies']['typescript'] = '<5'
            path.write_text(json.dumps(manifest), encoding='utf-8')

        stale = self.run_plan(metadata_source=metadata, before_worker=stale_manifest)
        self.assertFalse(stale['ok'])
        self.assertEqual(stale['error']['code'], 'WORKSPACE_MANIFEST_LOCK_MISMATCH')

    def test_resolves_scope_selects_bounded_candidates_and_holds_other_owner(self):
        result = self.run_plan()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['constraintModel']['status'], 'SAT_IN_MODELED_DOMAIN')
        self.assertEqual(result['constraintModel']['domain']['selection'], 'SERVER_BOUNDED')
        self.assertEqual(result['intent']['candidateSelection']['proofScope'], 'SELECTED_DOMAIN_ONLY')
        self.assertTrue(result['intent']['candidateSelection']['truncated'])
        targets = result['intent']['targets']
        self.assertEqual(targets[0]['versions'], ['3.0.1'])
        self.assertEqual(len(targets[1]['versions']), 8)
        self.assertIn('5.0.0', targets[1]['versions'])
        self.assertEqual(result['coverage']['heldDeclarationCount'], 1)
        self.assertFalse(result['executionReady'])
        self.assertEqual(result['installation']['status'], 'NOT_RUN')

    def test_ambiguous_name_returns_selectors_before_solver(self):
        request = {**self.request, 'scope': [{'selector': {'name': 'kleur'},
                                            'approvedLines': [3], 'exactTarget': '3.0.1'}]}
        result = self.run_plan(request)
        self.assertEqual(result['error']['code'], 'AMBIGUOUS_SCOPE')
        self.assertEqual(result['error']['matchCount'], 2)
        self.assertEqual([row['owner'] for row in result['error']['validSelectors']], ['packages/a', 'packages/b'])

    def test_drift_between_discovery_and_plan_is_rejected(self):
        result = self.run_plan(before_worker=lambda: (self.root / '.npmrc').write_text('save-exact=true'))
        self.assertEqual(result['error']['code'], 'STALE_WORKSPACE_INVENTORY')

    def test_exact_unknown_target_is_not_replaced_by_baseline(self):
        request = json.loads(json.dumps(self.request))
        request['scope'][0]['exactTarget'] = '3.0.9'
        result = self.run_plan(request)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['constraintModel']['status'], 'UNKNOWN')
        self.assertEqual(result['intent']['targets'][0]['versions'], ['3.0.9'])
        self.assertIsNone(result['candidateId'])

    def test_truncated_unsat_suggests_expansion_and_explicit_v2_stays_explicit(self):
        self.fixture.lock['packages']['node_modules/kleur']['peerDependencies'] = {'typescript': '^5'}
        self.fixture.save()
        def metadata(name):
            versions = ['5.0.0', '5.0.1', '5.0.2'] if name == 'typescript' else ['3.0.0', '3.0.1', '4.0.0']
            records = {v: {'name': name, 'version': v, 'dist': {
                'tarball': f'{REGISTRY}/{name}/-/{name}-{v}.tgz'}} for v in versions}
            if name == 'kleur':
                records['3.0.0']['peerDependencies'] = {'typescript': '^5'}
                records['3.0.1']['peerDependencies'] = {'typescript': '5.0.1'}
            return {'versions': records}, True
        small = self.run_plan({**self.request, 'candidateLimit': 2}, metadata_source=metadata)
        self.assertTrue(small['ok'], small)
        self.assertEqual(small['constraintModel']['status'], 'UNSAT_IN_MODELED_DOMAIN')
        self.assertEqual(small['action']['kind'], 'EXPAND_SELECTED_DOMAIN')
        large = self.run_plan({**self.request, 'candidateLimit': 3}, metadata_source=metadata)
        self.assertEqual(large['constraintModel']['status'], 'SAT_IN_MODELED_DOMAIN')
        self.assertFalse(large['intent']['candidateSelection']['truncated'])
        explicit = {'schemaVersion': 2, 'goalKind': 'TARGETED_UPGRADE', 'registry': REGISTRY,
                    'inventoryDigest': observe_npm_workspace(self.root)['inventoryDigest'],
                    'environment': {}, 'topology': None, 'targets': [
            {'selector': {'owner': 'packages/a', 'section': 'dependencies', 'name': 'kleur'},
             'approvedLines': [3], 'versions': ['3.0.1'], 'exactTarget': '3.0.1'},
            {'selector': {'owner': '.', 'section': 'devDependencies', 'name': 'typescript'},
             'approvedLines': [5], 'versions': ['5.0.2', '5.0.0'], 'exactTarget': None}]}
        legacy = self.run_plan(explicit, metadata_source=metadata)
        self.assertEqual(legacy['constraintModel']['status'], 'UNSAT_IN_MODELED_DOMAIN')
        self.assertEqual(legacy['intent']['targets'], explicit['targets'])
        self.assertNotIn('candidateSelection', legacy['intent'])

    def test_already_satisfied_target_is_not_a_completed_upgrade(self):
        request = json.loads(json.dumps(self.request))
        request['scope'][0]['exactTarget'] = '3.0.0'
        result = self.run_plan(request)
        self.assertEqual(result['error']['code'], 'WORKSPACE_TARGET_ALREADY_SATISFIED')

    def test_noop_ambiguity_and_authority_expansion_are_not_inferred(self):
        cases = [({**self.request, 'scope': [{'selector': {'name': 'kleur'}, 'approvedLines': [3]}]},
                  'WORKSPACE_TARGETED_GOAL_REQUIRES_EXACT_TARGET'),
                 ({**self.request, 'candidateLimit': True}, 'INVALID_WORKSPACE_CANDIDATE_LIMIT'),
                 ({**self.request, 'topology': {}}, 'INVALID_WORKSPACE_GOAL_REQUEST'),
                 ({**self.request, 'packuments': {}}, 'INVALID_WORKSPACE_GOAL_REQUEST')]
        for request, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(GoalContractError, code):
                validate_goal_request(request)
