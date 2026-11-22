"""No-network preflight contract and negative controls."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

FILE = Path(__file__).with_name('knowledge_gate.py')
spec = importlib.util.spec_from_file_location('cloud_gate', FILE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def row(source='verified-pages', status='found', recipe=True):
    return dict(ok=True, authority='ADVISORY_ONLY', snapshotSource=source, status=status,
                recipes=[{'id': 'align-react-renderers', 'source': 'https://github.com/levencode-labs/deploom-npm'}] if recipe else [],
                observations=[])


class KnowledgeGateTests(unittest.TestCase):
    def test_must_use_signed_pages_and_the_exact_control(self):
        gate.verify_control(row())
        for resp in (row(source='bundled'), row(source=None), row(status='no-matching-record'), row(recipe=False)):
            with self.subTest(resp=resp), self.assertRaises(ValueError):
                gate.verify_control(resp)

    def test_no_matching_record_is_normal_but_not_for_bundled(self):
        data = [{'caseId': 'example', 'package': 'typescript', 'queryIndex': 1, 'lookupStatus': 'QUERY_PENDING'}]
        result = gate.apply_responses(data, [row(), row(status='no-matching-record', recipe=False)])
        self.assertEqual(result[0]['lookupStatus'], 'no-matching-record')
        self.assertIs(result[0]['matchedRecord'], False)
        self.assertNotIn('queryIndex', result[0])
        with self.assertRaises(ValueError):
            gate.apply_responses([{'caseId': 'example', 'queryIndex': 1}], [row(), row(source='bundled')])

    def test_ranges_are_not_invented_exact_versions(self):
        catalog = dict(cases=[dict(id='one', targets=[dict(name='vite', **{'from': '^7.0.4', 'to': '8.1.6'}),
                                                      dict(name='typescript', **{'from': '5.7.3', 'to': '6.0.3'})])])
        queries, specs = gate.query_plan(catalog)
        self.assertEqual(queries, [gate.CONTROL, ('typescript', '5.7.3', '6.0.3')])
        self.assertEqual(specs[0]['lookupStatus'], 'UNRESOLVED_DECLARATION_RANGE')
        self.assertEqual(specs[1]['queryIndex'], 1)

    def test_execute_fails_closed_on_bundled(self):
        async def fake(*_):
            return [row(source='bundled')]
        with patch.object(gate, 'check_public_wheel', return_value={'version': gate.VERSION}), \
             patch.object(gate, 'refresh_signed_pages'), \
             patch.object(gate, 'lookup_mcp', fake):
            with self.assertRaisesRegex(ValueError, 'PAGES_NOT_VERIFIED'):
                gate.execute({'cases': []}, {}, Path('/fake.whl'))

    def test_execute_stores_no_imaginary_plan_impact(self):
        async def fake(*_):
            return [row(), row(status='no-matching-record', recipe=False)]
        with patch.object(gate, 'check_public_wheel', return_value={'version': gate.VERSION}), \
             patch.object(gate, 'refresh_signed_pages'), \
             patch.object(gate, 'lookup_mcp', fake):
            result = gate.execute({'cases': [{'id': 'example', 'targets': [
                {'name': 'typescript', 'from': '5.7.3', 'to': '6.0.3'}]}]}, {}, Path('/fake.whl'))
        self.assertEqual(result['status'], 'PASS_VERIFIED_PAGES')
        self.assertEqual(result['transitions'][0]['lookupStatus'], 'no-matching-record')
        self.assertEqual(result['transitions'][0]['planImpact'], 'NOT_EVALUATED')
        self.assertFalse(result['transitions'][0]['shownToAgent'])


if __name__ == '__main__':
    unittest.main()
