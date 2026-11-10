#!/usr/bin/env python3
"""Qualify an installed migration candidate outside the checkout; no model calls.

Run with a clean venv's Python after installing the specified wheel. Test fixtures
come from the checkout; product modules and Core must come from site-packages.
Native npm/Yarn executables must already be provisioned on PATH.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import sys
import time
import unittest
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    if Path.cwd().resolve().is_relative_to(repo) or os.environ.get('PYTHONPATH'):
        raise RuntimeError('Run outside the checkout with PYTHONPATH unset')
    if args.out.exists() or args.out.with_suffix('.log').exists():
        raise RuntimeError('Qualification outputs must be new')
    import deploom_mcp
    from deploom_mcp.distribution import resolve_core_dir, release_manifest_path, blueprint_schema_path, skill_root
    package = Path(deploom_mcp.__file__).resolve().parent
    if not package.is_relative_to(Path(sys.prefix).resolve()) or package.is_relative_to(repo):
        raise RuntimeError('Product must be imported from the active clean environment')
    core = resolve_core_dir()
    if core != package / '_core':
        raise RuntimeError('Core is not bundled in the installed package')
    manifest = json.loads(release_manifest_path().read_bytes())
    if manifest != json.loads((repo / 'release-manifest.json').read_bytes()):
        raise RuntimeError('Installed manifest differs from the candidate checkout')
    for name, expected in manifest['core']['files'].items():
        if hashlib.sha256((core / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Core byte mismatch: ' + name)
    for key, workspace in [('schemaSha256', False), ('workspaceSchemaSha256', True)]:
        if hashlib.sha256(blueprint_schema_path(workspace=workspace).read_bytes()).hexdigest() != manifest[key]:
            raise RuntimeError('Schema mismatch: ' + key)
    contract_bytes = (skill_root() / 'contract.json').read_bytes()
    if hashlib.sha256(contract_bytes).hexdigest() != manifest['skillManifestSha256']:
        raise RuntimeError('Skill manifest mismatch')
    contract = json.loads(contract_bytes)
    for name, expected in contract['files'].items():
        if hashlib.sha256((skill_root() / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Bundled Skill byte mismatch: ' + name)
    with zipfile.ZipFile(args.wheel) as archive:
        record = next(n for n in archive.namelist() if n.endswith('.dist-info/RECORD'))
        rows = list(csv.reader(io.StringIO(archive.read(record).decode())))
        if set(r[0] for r in rows) != set(archive.namelist()):
            raise RuntimeError('Incomplete wheel RECORD')
        for name, digest, size in rows:
            if not digest:
                if name != record:
                    raise RuntimeError('Unhashed wheel member')
                continue
            data = archive.read(name)
            actual = 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
            if digest != actual or len(data) != int(size):
                raise RuntimeError('Invalid wheel member: ' + name)
            if name.startswith('deploom_mcp/') and (package / name.split('/', 1)[1]).read_bytes() != data:
                raise RuntimeError('Installed bytes differ from wheel: ' + name)
    sys.path.insert(0, str(core))
    sys.path.insert(0, str(repo / 'upstream/deploom/tests'))
    sys.path.insert(0, str(repo / 'tests'))
    os.environ['DEPLOOM_NATIVE_AUDIT_TESTS'] = '1'
    suites = [
        'test_audit_stdio', 'test_audit_native', 'test_workspace_goal_request',
        'test_project_capabilities', 'test_migration_evidence', 'test_distribution',
        'test_workspace_partial_domain', 'test_workspace_bounded_selection',
        'test_workspace_registry_metadata', 'test_workspace_registry_metadata_http',
        'test_npm_semver_batch', 'test_registry_advisory_evidence',
        'test_workspace_health_evidence', 'test_workspace_health_policy',
        'test_workspace_peer_model', 'test_workspace_inventory',
        'test_migration_goal_integration', 'test_skill_contract', 'test_skill_integrity',
    ]
    results = []
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.with_suffix('.log').open('x', encoding='utf-8') as log:
        for name in suites:
            start = time.monotonic()
            result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName(name))
            row = {'suite': name, 'tests': result.testsRun, 'failures': len(result.failures),
                   'errors': len(result.errors), 'skips': len(result.skipped),
                   'seconds': round(time.monotonic() - start, 6)}
            results.append(row)
            print(json.dumps(row), flush=True)
    origins = {}
    for name in ('workspace_registry_metadata', 'workspace_peer_model', 'npm_semver_batch', 'dependency_live_roadmap_generator'):
        path = Path(sys.modules[name].__file__).resolve()
        if not path.is_relative_to(core):
            raise RuntimeError('Wrong Core module origin: ' + name)
        origins[name] = str(path)
    report = {
        'status': 'PASS' if all(not (r['failures'] or r['errors'] or r['skips']) for r in results) else 'INCOMPLETE',
        'wheelSha256': hashlib.sha256(args.wheel.read_bytes()).hexdigest(),
        'packagePath': str(package), 'corePath': str(core), 'moduleOrigins': origins,
        'coreRevision': manifest['core']['revision'], 'coreFilesVerified': len(manifest['core']['files']),
        'recordFilesVerified': len(rows), 'python': sys.version,
        'version': importlib.metadata.version('deploom-mcp'), 'skillVersion': contract['contractVersion'],
        'testSources': [str(repo / 'tests'), str(repo / 'upstream/deploom/tests')],
        'buildTestsUseCheckoutHelper': True, 'clientLoadedSkillAttested': False,
        'results': results,
    }
    with args.out.open('x', encoding='utf-8') as output:
        output.write(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'tests': sum(r['tests'] for r in results), 'wheelSha256': report['wheelSha256']}))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
