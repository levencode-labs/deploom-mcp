import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from case_knowledge import transitions

class CaseKnowledgeTests(unittest.TestCase):
    def test_exact_pair_without_resolution(self):
        case={'id':'example','targets':[{'name':'typescript','from':'5.7.3','to':'6.0.3'}]}
        r=transitions(case,None)
        self.assertEqual(r[0]['resolutionProvenance'],'PINNED_DECLARATION')
        self.assertFalse(r[0]['shownToAgent'])
    def test_range_requires_exact_resolution(self):
        case={'id':'example','targets':[{'name':'eslint','from':'^8.6.0','to':'9.39.4'}]}
        with self.assertRaisesRegex(ValueError,'EXACT_LOCK_VERSION_REQUIRED'):
            transitions(case,None)
        res={'schema':'deploom-exact-transitions-v1','caseId':'example','transitions':[{'package':'eslint','fromVersion':'8.57.1','toVersion':'9.39.4'}]}
        r=transitions(case,res)
        self.assertEqual(r[0]['resolutionProvenance'],'OPERATOR_LOCK_RESOLUTION_NEEDS_ORACLE')
    def test_exact_target_cannot_be_changed(self):
        case={'id':'example','targets':[{'name':'eslint','from':'^8.6.0','to':'9.39.4'}]}
        res={'schema':'deploom-exact-transitions-v1','caseId':'example','transitions':[{'package':'eslint','fromVersion':'8.57.1','toVersion':'9.39.3'}]}
        with self.assertRaisesRegex(ValueError,'PINNED_TARGET_VERSION_CHANGED'):
            transitions(case,res)

if __name__=='__main__':unittest.main()
