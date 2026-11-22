#!/usr/bin/env python3
"""Exact, source-bound per-case MCP knowledge lookup before each migration.

Exact transitions for range declarations must be independently resolved from the
pinned lock, supplied in a JSON file, and checked later by the acceptance oracle.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from knowledge_gate import (SEMVER, CONTROL, RECIPE,
                            check_public_wheel, lookup_mcp, refresh_signed_pages,
                            safe_environment, verify_control, write_exclusive)


def transitions(case, resolutions):
    if resolutions:
        if resolutions.get('schema') != 'deploom-exact-transitions-v1' or resolutions.get('caseId') != case['id']:
            raise ValueError('EXACT_TRANSITION_SCHEMA_OR_CASE_MISMATCH')
        by_name = {row['package']: row for row in resolutions['transitions']}
    else:
        by_name = {}
    records = []
    for item in case['targets']:
        name = item['name']
        resolved = by_name.get(name)
        source = resolved or {'fromVersion': item['from'], 'toVersion': item['to']}
        from_ver, to_ver = source['fromVersion'], source['toVersion']
        if not SEMVER.fullmatch(from_ver) or not SEMVER.fullmatch(to_ver):
            raise ValueError('EXACT_LOCK_VERSION_REQUIRED_' + name)
        if SEMVER.fullmatch(item['from']) and from_ver != item['from']:
            raise ValueError('PINNED_FROM_VERSION_CHANGED_' + name)
        if SEMVER.fullmatch(item['to']) and to_ver != item['to']:
            raise ValueError('PINNED_TARGET_VERSION_CHANGED_' + name)
        records.append({'caseId': case['id'], 'package': name, 'fromVersion': from_ver,
                        'toVersion': to_ver, 'declarationFrom': item['from'],
                        'targetRequested': item['to'], 'resolutionProvenance':
                        'PINNED_DECLARATION' if not resolved else 'OPERATOR_LOCK_RESOLUTION_NEEDS_ORACLE',
                        'shownToAgent': False, 'planImpact': 'NOT_EVALUATED', 'impactEvidence': []})
    if len(by_name) != len([x for x in case['targets'] if x['name'] in by_name]):
        raise ValueError('EXTRANEOUS_EXACT_RESOLUTION')
    return records


def run(args):
    catalog = json.loads(args.catalog.read_text(encoding='utf-8'))
    case = next((c for c in catalog['cases'] if c['id'] == args.case), None)
    if case is None or catalog['schema'] != 'deploom-cloud-extreme-v1':
        raise ValueError('UNKNOWN_CASE_OR_CATALOG')
    if case['route'] == 'pnpm-unsupported-control':
        raise ValueError('CONTROL_ONLY_NO_UPGRADE_ACCEPTANCE')
    gate = json.loads(args.gate.read_text())
    if gate.get('status') != 'PASS_VERIFIED_PAGES' or gate.get('control', {}).get('snapshotSource') != 'verified-pages':
        raise ValueError('GLOBAL_KNOWLEDGE_PREFLIGHT_MISSING')
    source = args.source.resolve(strict=True)
    head = subprocess.check_output(['git','rev-parse','HEAD'], cwd=source, text=True, timeout=30).strip()
    if head != case['sha']:
        raise ValueError('SOURCE_SHA_MISMATCH')
    lockfile = source / case['lockfile']
    if not lockfile.is_file():
        raise ValueError('PINNED_LOCKFILE_MISSING')
    with lockfile.open('rb') as stream:
        lock_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    resolutions = json.loads(args.resolutions.read_text()) if args.resolutions else None
    rows = transitions(case, resolutions)
    build = check_public_wheel(args.wheel)
    records_dir = args.records_dir.expanduser().absolute()
    env = safe_environment(records_dir)
    refresh_signed_pages(env)
    requests = [CONTROL] + [(r['package'], r['fromVersion'], r['toVersion']) for r in rows]
    replies = asyncio.run(lookup_mcp(env, requests))
    if len(replies) != len(requests):
        raise ValueError('MCP_REPLY_COUNT_MISMATCH')
    verify_control(replies[0])
    receipt_rows = []
    for i, row in enumerate(rows, 1):
        result = replies[i]
        if result.get('snapshotSource') != 'verified-pages' or result.get('status') not in ('found', 'no-matching-record'):
            raise ValueError('SIGNED_KNOWLEDGE_NOT_VERIFIED_FOR_' + row['package'])
        row.update(lookupStatus=result['status'], matchedRecord=(result['status']=='found'),
                   recipes=result.get('recipes', []), observations=result.get('observations', []),
                   snapshotSource='verified-pages')
        receipt_rows.append(row)
    return {'status':'PASS_VERIFIED_PAGES', 'caseId':case['id'], 'sourceSha':head,
            'lockfileSha256':lock_sha, 'wheel':build, 'control':{'query':list(CONTROL),'recipe':RECIPE},
            'transitions':receipt_rows, 'notes':'Plan impact stays NOT_EVALUATED until linked agent plan evidence is reviewed.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',type=Path,default=Path(__file__).with_name('cases.json'))
    parser.add_argument('--case',required=True)
    parser.add_argument('--source',type=Path,required=True,help='Pinned clean checkout')
    parser.add_argument('--gate',type=Path,required=True)
    parser.add_argument('--wheel',type=Path,required=True)
    parser.add_argument('--records-dir',type=Path,required=True)
    parser.add_argument('--resolutions',type=Path,help='Exact installed and target versions with lock provenance')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    try:
        result=run(args)
    except Exception as exc:
        result={'status':'NO_GO','reason':str(exc),'caseId':args.case}
    write_exclusive(args.out,result)
    print(json.dumps({'status':result['status'],'reason':result.get('reason'),'receipt':str(args.out)}))
    return 0 if result['status']=='PASS_VERIFIED_PAGES' else 2

if __name__=='__main__':
    sys.exit(main())
