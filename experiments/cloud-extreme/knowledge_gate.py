#!/usr/bin/env python3
"""No-model Codex Cloud gate for the signed DepLoom Pages knowledge snapshot.

Use the published wheel; never treat bundled examples as proof of Pages access.
The receipt is public-data only and must precede all paid experiment sessions.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys

VERSION = '0.2.27'
WHEEL_HASH = '5bd42bb881ca4ccb8694694ba1a4819eb8e48ec807f4bc4c40bbb5498dd630bb'
CONTROL = ('react-dom', '18.2.0', '18.3.1')
RECIPE = 'align-react-renderers'
SEMVER = re.compile(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z')


def check_public_wheel(wheel: Path) -> dict:
    if wheel.name != f'deploom_mcp-{VERSION}-py3-none-any.whl' or not wheel.is_file():
        raise ValueError('RELEASE_WHEEL_ABSENT_OR_WRONG_VERSION')
    with wheel.open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    if sha != WHEEL_HASH:
        raise ValueError('RELEASE_WHEEL_SHA256_MISMATCH')
    dist = importlib.metadata.distribution('deploom-mcp')
    if dist.version != VERSION:
        raise ValueError('INSTALLED_WHEEL_VERSION_MISMATCH')
    files = [f for f in (dist.files or ()) if str(f).startswith('deploom_mcp/') and str(f).endswith('.py')]
    if not files:
        raise ValueError('INSTALLED_PACKAGE_RECORD_UNAVAILABLE')
    for item in files:
        if not item.hash or item.hash.mode != 'sha256':
            raise ValueError('INSTALLED_PACKAGE_RECORD_UNHASHED')
        from base64 import urlsafe_b64encode
        with dist.locate_file(item).open('rb') as stream:
            digest = urlsafe_b64encode(hashlib.file_digest(stream, 'sha256').digest()).rstrip(b'=').decode()
        if digest != item.hash.value:
            raise ValueError('INSTALLED_PACKAGE_RECORD_MISMATCH')
    return {'version': VERSION, 'wheelSha256': sha, 'verifiedInstalledPythonFiles': len(files)}


def safe_environment(records_dir: Path) -> dict:
    if os.getenv('DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE') == '1' or os.getenv('DEPLOOM_MCP_LOCAL_CACHE_ONLY') == '1':
        raise ValueError('EXTERNAL_KNOWLEDGE_DISABLED')
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    env['DEPLOOM_MCP_RECORDS_DIR'] = str(records_dir)
    if not records_dir.is_absolute() or records_dir.is_symlink():
        raise ValueError('RECORDS_DIR_MUST_BE_ABSOLUTE_REGULAR_PATH')
    return env


def refresh_signed_pages(env: dict) -> None:
    code = ('from deploom_mcp.shared_records import refresh, directory; '
            'import sys; sys.exit(0 if refresh(directory()) else 3)')
    task = subprocess.run([sys.executable, '-c', code], env=env,
                          capture_output=True, timeout=160)
    if task.returncode != 0:
        raise ValueError('SIGNED_PAGES_REFRESH_FAILED')


def get_response(result) -> dict:
    if result.isError:
        raise ValueError('MCP_TOOL_ERROR')
    payload = result.structuredContent
    if not isinstance(payload, dict):
        if len(result.content) != 1 or result.content[0].type != 'text':
            raise ValueError('MCP_RESPONSE_NOT_JSON')
        payload = json.loads(result.content[0].text)
    if payload.get('ok') is not True or payload.get('authority') != 'ADVISORY_ONLY':
        raise ValueError('MCP_ADVISORY_AUTHORITY_MISMATCH')
    return payload


async def lookup_mcp(env: dict, queries: list[tuple[str, str, str]]) -> list[dict]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(command=sys.executable, args=['-m', 'deploom_mcp'], env=env)
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=35)
            results = []
            for package, before, after in queries:
                raw = await asyncio.wait_for(session.call_tool('get_shared_migration_evidence', {
                    'package': package, 'from_version': before, 'to_version': after,
                }), timeout=35)
                results.append(get_response(raw))
            return results


def query_plan(catalog: dict) -> tuple[list[tuple[str, str, str]], list[dict]]:
    queries = [CONTROL]
    specs = []
    for case in catalog['cases']:
        for target in case['targets']:
            before, after = target['from'], target['to']
            row = {'caseId': case['id'], 'package': target['name'],
                   'declaredFrom': before, 'requestedTo': after,
                   'matchedRecord': None, 'planImpact': 'NOT_EVALUATED',
                   'impactEvidence': [], 'shownToAgent': False}
            if SEMVER.fullmatch(before) and SEMVER.fullmatch(after):
                row['lookupStatus'] = 'QUERY_PENDING'
                row['queryIndex'] = len(queries)
                queries.append((target['name'], before, after))
            else:
                row['lookupStatus'] = 'UNRESOLVED_DECLARATION_RANGE'
                row['reason'] = 'Resolve exact installed/from and selected/to versions from the pinned lock before claiming a record match'
            specs.append(row)
    return queries, specs


def verify_control(response: dict) -> None:
    if response.get('snapshotSource') != 'verified-pages':
        raise ValueError('PAGES_NOT_VERIFIED_SOURCE_' + str(response.get('snapshotSource')))
    if response.get('status') != 'found' or not any(
        r.get('id') == RECIPE for r in response.get('recipes', ()) if isinstance(r, dict)
    ):
        raise ValueError('CONTROL_REACT_DOM_RECIPE_NOT_FOUND')


def apply_responses(rows: list[dict], results: list[dict]) -> list[dict]:
    for row in rows:
        index = row.pop('queryIndex', None)
        if index is None:
            continue
        evidence = results[index]
        if evidence.get('snapshotSource') != 'verified-pages':
            raise ValueError('CASE_SOURCE_NOT_VERIFIED_PAGES_' + row['caseId'])
        if evidence.get('status') not in ('found', 'no-matching-record'):
            raise ValueError('CASE_UNEXPECTED_LOOKUP_STATUS_' + row['caseId'])
        row['lookupStatus'] = evidence['status']
        row['matchedRecord'] = evidence['status'] == 'found'
        row['recipes'] = evidence.get('recipes', [])
        row['observations'] = evidence.get('observations', [])
    return rows


def execute(catalog: dict, env: dict, wheel: Path) -> dict:
    build = check_public_wheel(wheel)
    refresh_signed_pages(env)
    queries, rows = query_plan(catalog)
    results = asyncio.run(lookup_mcp(env, queries))
    if len(results) != len(queries):
        raise ValueError('MCP_RESPONSE_COUNT_MISMATCH')
    verify_control(results[0])
    for result in results[1:]:
        if result.get('snapshotSource') != 'verified-pages':
            raise ValueError('MCP_RESPONSE_SOURCE_CHANGED')
    return {'status': 'PASS_VERIFIED_PAGES', 'source': 'signed GitHub Pages cache',
            'authority': 'ADVISORY_ONLY', 'publicWheel': build,
            'control': {'query': list(CONTROL), 'snapshotSource': results[0]['snapshotSource'],
                        'status': results[0]['status'], 'recipeId': RECIPE},
            'transitions': apply_responses(rows, results),
            'notes': 'A match is advisory only. Range unresolved is not no-matching-record. No agent plan impact is inferred.'}


def write_exclusive(path: Path, receipt: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as out:
        json.dump(receipt, out, indent=2, ensure_ascii=False, sort_keys=True)
        out.write('\n')
        out.flush()
        os.fsync(out.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, required=True, help='Downloaded published exact release wheel')
    parser.add_argument('--records-dir', type=Path, required=True, help='Dedicated durable cache outside repo')
    parser.add_argument('--out', type=Path, required=True, help='New receipt path; never overwritten')
    parser.add_argument('--catalog', type=Path, default=Path(__file__).with_name('cases.json'))
    args = parser.parse_args()
    try:
        catalog = json.loads(args.catalog.read_text(encoding='utf-8'))
        if catalog.get('schema') != 'deploom-cloud-extreme-v1':
            raise ValueError('CASE_CATALOG_SCHEMA_MISMATCH')
        repo_dir = Path(__file__).resolve().parents[2]
        cache_dir = args.records_dir.expanduser().absolute()
        if cache_dir == repo_dir or repo_dir in cache_dir.parents:
            raise ValueError('RECORDS_DIR_INSIDE_REPO')
        if cache_dir == Path.home() or cache_dir == Path('/'):
            raise ValueError('RECORDS_DIR_UNSAFE')
        env = safe_environment(cache_dir)
        receipt = execute(catalog, env, args.wheel.expanduser())
    except Exception as exc:
        receipt = {'status': 'NO_GO', 'reason': str(exc), 'authority': 'ADVISORY_ONLY',
                   'note': 'No model calls; bundled is not proof of Pages access'}
    write_exclusive(args.out, receipt)
    print(json.dumps({'status': receipt['status'], 'receipt': str(args.out),
                      'reason': receipt.get('reason')}))
    return 0 if receipt['status'] == 'PASS_VERIFIED_PAGES' else 2


if __name__ == '__main__':
    sys.exit(main())
