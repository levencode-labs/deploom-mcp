import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from impact_review import review

LOOKUP={'status':'PASS_VERIFIED_PAGES','caseId':'case','sourceSha':'a'*40,'lockfileSha256':'b'*64,'transitions':[{'package':'typescript','lookupStatus':'no-matching-record'}]}
CLAIM={'schema':'deploom-plan-impact-v1','caseId':'case','entries':[{'package':'typescript','impact':'NO_MATCH','shownToAgent':False,'rationale':'Confirmed no match'}]}

class ImpactReviewTests(unittest.TestCase):
    def test_no_match_can_be_recorded_without_fabricating_use(self):
        r=review(LOOKUP,CLAIM,None,None,None)
        self.assertEqual(r['status'],'REVIEWED_CLAIMS_NOT_CAUSAL_PROOF')
        self.assertEqual(r['transitions'][0]['planImpact'],'NO_MATCH')
    def test_no_match_cannot_be_used_as_recipe(self):
        claim={**CLAIM,'entries':[{'package':'typescript','impact':'PLAN_GUIDED','shownToAgent':True,'rationale':'Claimed use'}]}
        with self.assertRaisesRegex(ValueError,'NO_MATCH_CANNOT_GUIDE'):
            review(LOOKUP,claim,None,None,None)
    def test_guided_requires_traces(self):
        lookup={**LOOKUP,'transitions':[{'package':'typescript','lookupStatus':'found','recipes':[{'id':'align-react-renderers'}]}]}
        claim={**CLAIM,'entries':[{'package':'typescript','impact':'PLAN_GUIDED','shownToAgent':True,'rationale':'Recipe affected plan'}]}
        with self.assertRaisesRegex(ValueError,'PLAN_GUIDED_REQUIRES'):
            review(lookup,claim,None,None,None)

if __name__=='__main__':unittest.main()
