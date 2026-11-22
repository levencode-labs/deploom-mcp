#!/usr/bin/env python3
"""Bind a reviewed plan-impact judgement to the signed per-case knowledge receipt.

Reviewer assertions are not causal proof. This script prevents unlinked claims;
it does not independently execute, assess, or re-score an upgrade.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from knowledge_gate import write_exclusive

STATES = {'NO_MATCH', 'NOT_SHOWN', 'CONSIDERED_NO_CHANGE', 'PLAN_GUIDED', 'UNKNOWN'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def review(lookup, claims, before, after, agent_trace):
    if lookup.get('status') != 'PASS_VERIFIED_PAGES':
        raise ValueError('SIGNED_MCP_LOOKUP_REQUIRED')
    if claims.get('schema') != 'deploom-plan-impact-v1' or claims.get('caseId') != lookup.get('caseId'):
        raise ValueError('CLAIM_SCHEMA_OR_CASE_MISMATCH')
    expected = {r['package']:r for r in lookup['transitions']}
    supplied = claims.get('entries', [])
    actual = [row.get('package') for row in supplied]
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise ValueError('EVERY_EXACT_TRANSITION_MUST_HAVE_ONE_CLAIM')
    if len(actual) != len(set(actual)):
        raise ValueError('DUPLICATE_TRANSITION_CLAIMS')
    final=[]
    for row in supplied:
        key=row['package']
        source=expected[key]
        state=row.get('impact')
        shown=row.get('shownToAgent')
        rationale=row.get('rationale')
        if state not in STATES or type(shown) is not bool or not isinstance(rationale, str) or len(rationale)>3000:
            raise ValueError('INVALID_IMPACT_CLAIM_'+key)
        if source['lookupStatus']=='no-matching-record':
            if state!='NO_MATCH' or shown:
                raise ValueError('NO_MATCH_CANNOT_GUIDE_PLAN_'+key)
        elif source['lookupStatus']=='found':
            if state=='NO_MATCH' or (not shown and state in {'CONSIDERED_NO_CHANGE','PLAN_GUIDED'}):
                raise ValueError('UNSUPPORTED_GUIDED_CLAIM_'+key)
            if state=='PLAN_GUIDED' and (not rationale.strip() or before is None or after is None or agent_trace is None):
                raise ValueError('PLAN_GUIDED_REQUIRES_BEFORE_AFTER_TRACE_AND_REASON_'+key)
        else:
            raise ValueError('LOOKUP_NOT_TERMINAL_'+key)
        final.append({**source, 'shownToAgent':shown,'planImpact':state,'reviewerRationale':rationale,
                      'impactEvidence':row.get('evidence', [])})
    return {'schema':'deploom-plan-impact-review-v1','status':'REVIEWED_CLAIMS_NOT_CAUSAL_PROOF',
            'caseId':lookup['caseId'],'sourceSha':lookup['sourceSha'],
            'lockfileSha256':lookup['lockfileSha256'],
            'snapshotSource':'verified-pages',
            'planBeforeSha256':digest(before) if before else None,
            'planAfterSha256':digest(after) if after else None,
            'agentTraceSha256':digest(agent_trace) if agent_trace else None,
            'transitions':final, 'note':'Independent oracle, differential experiment and real checks are separate.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lookup',type=Path,required=True)
    p.add_argument('--claims',type=Path,required=True)
    p.add_argument('--plan-before',type=Path)
    p.add_argument('--plan-after',type=Path)
    p.add_argument('--agent-trace',type=Path)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    try:
        data=review(json.loads(a.lookup.read_text()),json.loads(a.claims.read_text()),
                    a.plan_before,a.plan_after,a.agent_trace)
    except Exception as exc:
        data={'status':'NO_GO','reason':str(exc)}
    write_exclusive(a.out,data)
    print(json.dumps({'status':data['status'],'reason':data.get('reason'),'receipt':str(a.out)}))
    return 0 if data['status']=='REVIEWED_CLAIMS_NOT_CAUSAL_PROOF' else 2

if __name__=='__main__':
    sys.exit(main())
