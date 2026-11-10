"""Discovery must not fetch evidence, execute tools or infer host permission."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deploom_mcp.project_capabilities import get_project_capabilities


class ProjectCapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.manifest = {"name": "fixture", "version": "1.0.0"}
        self.lock = {"lockfileVersion": 3, "packages": {"": dict(self.manifest)}}
        self.save()

    def save(self):
        (self.root / "package.json").write_text(json.dumps(self.manifest))
        (self.root / "package-lock.json").write_text(json.dumps(self.lock))

    def test_standalone_routes_without_execution_or_network(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        with patch('subprocess.Popen', side_effect=AssertionError('discovery executed a command')), patch(
            'requests.Session.request', side_effect=AssertionError('discovery requested network evidence'),
        ), patch.dict(os.environ, {}, clear=True):
            result = get_project_capabilities(str(self.root))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['manager'], 'npm')
        self.assertEqual(result['workspaceKind'], 'STANDALONE')
        self.assertIsNone(result['inventoryDigest'])
        verify = next(route for route in result['routes'] if route['operation'] == 'VERIFY')
        self.assertEqual(verify['hostConfiguration'], 'MISSING')
        self.assertEqual(verify['authorization'], 'NOT_GRANTED_BY_DISCOVERY')
        self.assertFalse(result['executionReady'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_workspace_inventory_is_bound_but_settings_do_not_grant_permission(self):
        self.manifest['workspaces'] = ['packages/*']
        self.lock['packages'][''] = dict(self.manifest)
        member = {'name': '@fixture/app', 'version': '1.0.0'}
        self.lock['packages']['packages/app'] = member
        self.lock['packages']['node_modules/@fixture/app'] = {'link': True, 'resolved': 'packages/app'}
        (self.root / 'packages/app').mkdir(parents=True)
        (self.root / 'packages/app/package.json').write_text(json.dumps(member))
        self.save()
        with patch.dict(os.environ, {'DEPLOOM_WORKSPACE_POLICY_REGISTRY': 'https://registry.example.invalid'}, clear=True):
            result = get_project_capabilities(str(self.root))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['workspaceKind'], 'NPM_WORKSPACES')
        self.assertEqual(len(result['inventoryDigest']), 64)
        route = result['routes'][0]
        self.assertEqual(route['hostConfiguration'], 'PRESENT_NOT_VALIDATED')
        self.assertEqual(route['authorization'], 'NOT_GRANTED_BY_DISCOVERY')
        self.assertNotIn('registry.example.invalid', json.dumps(result))
        self.assertEqual(result['nextAction'], 'USE_WORKSPACE_REQUEST_V3')

    def test_configs_are_digest_bound_without_echoing_credentials(self):
        first = get_project_capabilities(str(self.root))
        (self.root / '.npmrc').write_text('//example.invalid/:_authToken=fixture-secret\n')
        second = get_project_capabilities(str(self.root))
        self.assertNotEqual(first['inputDigest'], second['inputDigest'])
        self.assertNotIn('fixture-secret', json.dumps(second))
        self.assertNotIn('example.invalid', json.dumps(second))

    def test_ambiguous_and_unsupported_inputs_fail_before_routing(self):
        (self.root / 'yarn.lock').write_text('# yarn lockfile v1\n')
        result = get_project_capabilities(str(self.root))
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'ambiguous-lockfiles')
        self.assertFalse(get_project_capabilities('relative')['ok'])

    def test_yarn_classic_does_not_advertise_standalone_npm_execution(self):
        (self.root / 'package-lock.json').unlink()
        (self.root / 'yarn.lock').write_text('# yarn lockfile v1\n')
        result = get_project_capabilities(str(self.root))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['manager'], 'yarn-classic')
        self.assertEqual([route['operation'] for route in result['routes']], ['PLAN'])
