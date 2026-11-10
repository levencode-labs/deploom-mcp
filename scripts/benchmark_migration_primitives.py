"""Local synthetic component timings. This is not an Agent-versus-MCP benchmark."""
from __future__ import annotations
import argparse
import ast
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from deploom_mcp.distribution import resolve_core_dir
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp import workspace_inventory as inventory
sys.path.insert(0, str(resolve_core_dir()))
from draft_npm_preflight import _npm_command
from npm_semver_batch import match_npm_semver_many
from dependency_live_roadmap_generator import DeadlineClock, LiveDataClient
from workspace_registry_metadata import fetch_workspace_packuments


def timed(fn):
    start = time.perf_counter()
    result = fn()
    return result, time.perf_counter() - start


def discovery(root):
    root.mkdir()
    manifest = {'name': 'benchmark', 'version': '1.0.0', 'workspaces': ['packages/*']}
    packages = {'': manifest}
    (root / 'package.json').write_text(json.dumps(manifest))
    for name in ('a', 'b'):
        member = {'name': '@fixture/'+name, 'version': '1.0.0'}
        folder = root / 'packages' / name
        folder.mkdir(parents=True)
        (folder / 'package.json').write_text(json.dumps(member))
        packages['packages/'+name] = member
        packages['node_modules/@fixture/'+name] = {'link': True, 'resolved': 'packages/'+name}
    (root / 'package-lock.json').write_text(json.dumps({'lockfileVersion': 3, 'packages': packages}))
    original_digest = inventory.observe_npm_workspace(root)['inventoryDigest']
    old_source = subprocess.check_output(['git', 'show', '60aef86:src/deploom_mcp/workspace_inventory.py'],
                                        cwd=ROOT, encoding='utf-8')
    tree = ast.parse(old_source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_discover')
    namespace = dict(vars(inventory))
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<baseline-discovery>', 'exec'), namespace)
    previous, current = namespace['_discover'], inventory._discover
    scandir = os.scandir
    rows = []
    noise = root / 'noise';noise.mkdir()
    for i in range(2100):
        (noise / str(i)).mkdir()
        if i not in (1799, 2099):
            continue
        for label, implementation in [('prior', previous), ('prefix', current)]:
            scans = 0
            def scan(path):
                nonlocal scans
                scans += 1
                return scandir(path)
            with patch.object(inventory, '_discover', implementation), patch.object(os, 'scandir', scan):
                started = time.perf_counter()
                try:
                    value = inventory.observe_npm_workspace(root)
                    status = 'COMPLETE'
                    assert value['inventoryDigest'] == original_digest
                except GoalContractError as exc:
                    status = exc.code
                seconds = time.perf_counter() - started
            rows.append({'noiseDirectories': i+1, 'implementation': label, 'seconds': seconds,
                         'scandirCalls': scans, 'status': status})
    return rows


def semver():
    node, npm = _npm_command()
    module = str(Path(npm).resolve().parent.parent / 'node_modules' / 'semver')
    pairs = [('>=1.0.'+str(i), '1.0.99') for i in range(100)]
    script = ("const s=require(process.argv[1]);const [r,v]=JSON.parse(require('fs').readFileSync(0,'utf8'));"
              "const o={includePrerelease:true,loose:true};if(s.validRange(r,o)===null)process.exit(2);"
              "process.stdout.write(JSON.stringify(s.satisfies(v,r,o)));")
    def scalar():
        return [json.loads(subprocess.run([node, '-e', script, module], input=json.dumps(pair),
            text=True, capture_output=True, check=True, timeout=5).stdout) for pair in pairs]
    rows = []
    for repetition in range(3):
        for name, fn, count in ([('scalar', scalar, 100), ('batch', lambda: match_npm_semver_many(pairs), 1)]
                                if repetition % 2 == 0 else
                                [('batch', lambda: match_npm_semver_many(pairs), 1), ('scalar', scalar, 100)]):
            answers, seconds = timed(fn)
            assert answers == [True]*100
            rows.append({'implementation': name, 'seconds': seconds, 'nodeProcesses': count})
    return {'comparisons': 100, 'runs': rows,
            'medianSeconds': {name: statistics.median(row['seconds'] for row in rows if row['implementation']==name)
                              for name in ('scalar', 'batch')}}


def metadata():
    active = peak = requests = 0
    lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal active, peak, requests
            with lock:
                active += 1;requests += 1;peak = max(peak, active)
            try:
                time.sleep(.1)
                name = self.path.strip('/')
                body = json.dumps({'name': name, 'versions': {'1.0.0': {'name': name, 'version': '1.0.0'}}}).encode()
                self.send_response(200);self.send_header('Content-Length', str(len(body)));self.end_headers()
                self.wfile.write(body)
            finally:
                with lock:active -= 1
        def log_message(self, *args):pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
    names = ['p'+str(i) for i in range(8)]
    rows = []
    expected = None
    try:
        for repetition in range(3):
            for parallel in ([False, True] if repetition % 2 == 0 else [True, False]):
                active = peak = requests = 0
                client = LiveDataClient(f'http://127.0.0.1:{server.server_port}', timeout=2, batch_size=8, sleep_sec=0,
                                        allow_registry_metadata_redirects=False)
                client.set_deadline(DeadlineClock(15))
                try:
                    value, seconds = timed(lambda: fetch_workspace_packuments(client, names) if parallel else
                                           {name: client.fetch_npm_metadata(name) for name in names})
                finally:client.session.close()
                if expected is None:expected=value
                assert value==expected and requests==8
                rows.append({'implementation': 'window4' if parallel else 'sequential', 'seconds': seconds,
                             'requests': requests, 'peakConcurrent': peak})
    finally:server.shutdown();server.server_close();thread.join(3)
    return {'names': 8, 'syntheticDelaySeconds': .1, 'runs': rows,
            'medianSeconds': {name: statistics.median(row['seconds'] for row in rows if row['implementation']==name)
                              for name in ('sequential', 'window4')}}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='deploom-components-') as temporary:
        report={'kind': 'SYNTHETIC_COMPONENT_MEASUREMENT', 'agentSpeedupProven': False,
                'python': sys.version, 'core': subprocess.check_output(['git','rev-parse','HEAD'], cwd=resolve_core_dir(), text=True).strip(),
                'discovery': discovery(Path(temporary)/'project'), 'semver': semver(), 'metadata': metadata()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:json.dump(report,stream,indent=2)
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
