"""Measure flat npm planning over Streamable HTTP on an isolated replay registry."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack, contextmanager
import hashlib
from http.server import ThreadingHTTPServer
import json
import logging
import math
import os
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from e2e_registry import Handler, PackageStore


@contextmanager
def replay_registry(dependencies, delay):
    class Replay(Handler):
        def do_GET(self):
            time.sleep(delay)
            super().do_GET()

    with ThreadingHTTPServer(("127.0.0.1", 0), Replay) as server:
        url = f"http://127.0.0.1:{server.server_port}"
        Replay.store = PackageStore({"packages": {
            name: {"versions": [{"version": "1.0.0"}, {"version": "1.1.0"}]}
            for name in dependencies
        }}, url)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield url, Replay.store.hits
        finally:
            server.shutdown()
            thread.join()


@contextmanager
def mcp_process(source, registry, state):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {key: value for key, value in os.environ.items() if not key.startswith("DEPLOOM_")}
    env.update(PYTHONPATH=str(source / "src"), PYTHONDONTWRITEBYTECODE="1",
               DEPLOOM_MCP_STATE_DIR=str(state), DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE="1",
               DEPLOOM_MCP_ALLOWED_PROJECT_REGISTRY=registry)
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "deploom_mcp", "--transport", "streamable-http", "--port", str(port)],
            cwd=source, env=env, stdout=log, stderr=log,
        )
        try:
            for _ in range(150):
                if proc.poll() is not None:
                    break
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                raise RuntimeError("MCP startup timed out")
            if proc.poll() is not None:
                log.seek(0)
                raise RuntimeError(log.read().decode("utf-8", errors="replace"))
            yield f"http://127.0.0.1:{port}/mcp"
        finally:
            proc.terminate()
            proc.wait(timeout=10)


def fixture(project, names, registry):
    dependencies = dict.fromkeys(names, "1.0.0")
    manifest = {"name": "cache-fixture", "version": "1.0.0", "dependencies": dependencies}
    packages = {"": manifest, **{
        f"node_modules/{name}": {"version": "1.0.0", "resolved": f"{registry}/{name}/-/{name}-1.0.0.tgz"}
        for name in names
    }}
    project.mkdir()
    (project / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    lock = {"name": manifest["name"], "version": "1.0.0", "lockfileVersion": 3, "packages": packages}
    (project / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")


def semantic_digest(payload):
    plan = {key: payload[key] for key in
            ("status", "policy", "summary", "decisionCompleteness", "decisionEvidence", "workspaceCoverage")
            if key in payload}
    assert plan["status"]["authority"] == "PLANNING_ONLY" and not payload["partial"]["partial"], plan
    rows = payload["dependencies"]
    assert len(rows) == plan["summary"]["totalDependencies"] > 0, "incomplete dependency projection"
    plan["dependencies"] = rows
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()


async def connect(stack, url):
    streams = await stack.enter_async_context(streamable_http_client(url))
    session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
    await session.initialize()
    return session


async def sample(session, project, ttl=3600):
    started = time.perf_counter()
    result = await session.call_tool("get_upgrade_plan", {
        "project_path": str(project), "registry_metadata_ttl": ttl, "deadline_seconds": 30,
        "draft_max_candidates": 1, "use_system_proxy": False,
    })
    elapsed = time.perf_counter() - started
    payload = result.structured_content
    assert not result.is_error and payload and payload.get("ok"), payload or result.content
    return elapsed, semantic_digest(payload), len(json.dumps(payload, ensure_ascii=False).encode())


async def run(source, state, registry, replay_hits, projects, concurrency):
    rows = []
    expected = {}

    async def measure(mode, calls, *, ttl=3600):
        start = len(replay_hits)
        results = await asyncio.gather(*(sample(session, project, ttl) for session, project in calls))
        for (_, project), (elapsed, digest, size) in zip(calls, results, strict=True):
            assert digest == expected.setdefault(project.name, digest), (mode, project.name, digest, expected[project.name])
            rows.append({"mode": mode, "seconds": elapsed, "fixture": project.name, "semanticSha256": digest,
                         "structuredBytes": size})
        new_hits = replay_hits[start:]
        assert len(new_hits) == sum(not hit.endswith(".tgz") for hit in new_hits), new_hits
        return len(new_hits)

    burst = projects[-1]
    single = projects[:-1]
    counts = {}
    with mcp_process(source, registry, state) as url:
        async with AsyncExitStack() as stack:
            sessions = [await connect(stack, url) for _ in range(concurrency)]
            for idx, project in enumerate(single):
                calls = [(sessions[0], project)]
                if idx % 2:
                    counts[("disabled", project.name)] = await measure("disabled", calls, ttl=0)
                counts[("cold", project.name)] = await measure("cold", calls)
                counts[("warm_other_session", project.name)] = await measure("warm_other_session", [(sessions[-1], project)])
                if not idx % 2:
                    counts[("disabled", project.name)] = await measure("disabled", calls, ttl=0)
            for mode in ("concurrent_cold", "concurrent_warm"):
                counts[(mode, burst.name)] = await measure(mode, [(session, burst) for session in sessions])

    with mcp_process(source, registry, state) as url:
        async with AsyncExitStack() as stack:
            session = await connect(stack, url)
            for project in single:
                counts[("after_restart", project.name)] = await measure("after_restart", [(session, project)])

    modes = dict.fromkeys(row["mode"] for row in rows)
    summary = {}
    for mode in modes:
        times = sorted(row["seconds"] for row in rows if row["mode"] == mode)
        summary[mode] = {"n": len(times), "p50Seconds": statistics.median(times),
                         "p95Seconds": times[math.ceil(len(times) * .95) - 1],
                         "upstreamMetadataGets": sum(count for (arm, _), count in counts.items() if arm == mode)}
    return {"summary": summary, "rows": rows,
            "upstreamCounts": [{"mode": arm, "fixture": name, "gets": count} for (arm, name), count in counts.items()]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=12)
    parser.add_argument("--dependencies", type=int, default=16)
    parser.add_argument("--delay-ms", type=float, default=80)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--expect-reuse", action="store_true")
    args = parser.parse_args()
    if args.repeats < 1 or args.dependencies < 2 or args.concurrency < 2 or args.delay_ms < 0:
        parser.error("positive samples, at least two dependencies and sessions, and nonnegative delay are required")
    source = args.source.resolve()
    logging.disable(logging.INFO)
    batches = [[f"deploom-replay-{i:03}-{n:03}" for n in range(args.dependencies)]
               for i in range(args.repeats + 1)]
    with tempfile.TemporaryDirectory(prefix="deploom-http-cache-") as raw:
        temporary = Path(raw).resolve()
        with replay_registry([name for names in batches for name in names], args.delay_ms / 1000) as (registry, hits):
            projects = [temporary / f"project-{i:03}" for i in range(len(batches))]
            for project, names in zip(projects, batches, strict=True):
                fixture(project, names, registry)
            result = asyncio.run(run(source, temporary / "state", registry, hits, projects, args.concurrency))
    summary = result["summary"]
    for mode in ("cold", "disabled"):
        assert summary[mode]["upstreamMetadataGets"] == args.dependencies * args.repeats, summary
    if args.expect_reuse:
        for mode in ("warm_other_session", "after_restart", "concurrent_warm"):
            assert summary[mode]["upstreamMetadataGets"] == 0, summary
        assert summary["concurrent_cold"]["upstreamMetadataGets"] == args.dependencies, summary
    revisions = {key: subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()
                 for key, path in (("mcp", source), ("core", source / "upstream" / "deploom"))}
    from importlib.metadata import version

    result.update(revisions=revisions, python=sys.version, mcpSdk=version("mcp"),
                  delayMs=args.delay_ms, dependencies=args.dependencies,
                  concurrency=args.concurrency, mcpTransport="streamable-http", scope="flat npm planning",
                  externalEvidenceDisabled=True, semanticPayloadsMatch=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
