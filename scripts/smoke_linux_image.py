"""Smoke the real Linux container entrypoint, restricted tools and mounted cache."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack, contextmanager
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))
from measure_http_cache import connect, fixture, replay_registry, semantic_digest

PROJECT = "/projects/sample"
STATE = "/data/deploom-mcp"
DEPENDENCIES = ["deploom-image-smoke-a", "deploom-image-smoke-b"]


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


@contextmanager
def writable_state(image, scratch):
    state = Path(scratch) / "state"
    state.mkdir()
    # The scratch root is private. The non-root container needs a writable bind mount.
    state.chmod(0o777)
    try:
        yield state
    finally:
        docker("run", "--rm", "--network", "none", "--user", "0:0",
               "--mount", f"type=bind,src={state},dst={STATE}",
               "--entrypoint", "chown", image, "-R", f"{os.getuid()}:{os.getgid()}", STATE)


@contextmanager
def container(image, project, state, registry):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    cid = docker(
        "run", "--detach", "--network", "host",
        "--mount", f"type=bind,src={project},dst={PROJECT},readonly",
        "--mount", f"type=bind,src={state},dst={STATE}",
        "--env", f"DEPLOOM_MCP_STATE_DIR={STATE}",
        "--env", f"DEPLOOM_MCP_ALLOWED_PROJECT_REGISTRY={registry}",
        "--env", "DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE=1",
        image, "--host", "127.0.0.1", "--port", str(port), "--pilot-project", PROJECT,
    )
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if docker("inspect", "--format", "{{.State.Running}}", cid) != "true":
                    raise RuntimeError("Container exited during startup")
                time.sleep(0.1)
        else:
            raise RuntimeError("Container startup timed out")
        yield f"http://127.0.0.1:{port}/mcp", cid
    except Exception:
        subprocess.run(["docker", "logs", cid], check=False)
        raise
    finally:
        docker("rm", "--force", cid)


async def plan(url, ttl=3600, *, check_tools=False):
    async with AsyncExitStack() as stack:
        session = await connect(stack, url)
        if check_tools:
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {"get_project_capabilities", "get_upgrade_plan"}
            caps = await session.call_tool("get_project_capabilities", {"project_path": PROJECT})
            assert not caps.is_error and caps.structured_content["workspaceKind"] == "STANDALONE"
            for name, arguments, code in (
                ("get_project_capabilities", {"project_path": STATE}, "pilot-project-denied"),
                ("get_upgrade_plan", {"project_path": PROJECT, "workspace_request": {}}, "pilot-options-denied"),
                ("get_goal_plan", {"project_path": PROJECT, "goal": {}}, "pilot-tool-disabled"),
            ):
                denied = await session.call_tool(name, arguments)
                assert denied.is_error and denied.structured_content["error"]["code"] == code, denied
        result = await session.call_tool("get_upgrade_plan", {
            "project_path": PROJECT, "registry_metadata_ttl": ttl, "deadline_seconds": 30,
        })
        assert not result.is_error and result.structured_content, result
        return semantic_digest(result.structured_content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--version", required=True)
    parser.add_argument("--core", required=True)
    args = parser.parse_args()

    platform = docker("image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", args.image)
    assert platform == "linux/amd64", platform
    with tempfile.TemporaryDirectory(prefix="deploom-image-smoke-") as scratch:
        project = Path(scratch) / "sample"
        with writable_state(args.image, scratch) as state, replay_registry(DEPENDENCIES, 0) as (registry, hits):
            fixture(project, DEPENDENCIES, registry)
            before = {path.name: path.read_bytes() for path in project.iterdir()}
            with container(args.image, project, state, registry) as (url, cid):
                version, core, uid, node = docker("exec", cid, "python", "-c",
                    "import json, os, subprocess; from deploom_mcp import __version__; "
                    "from deploom_mcp.distribution import release_manifest_path; "
                    "print(__version__, json.loads(release_manifest_path().read_bytes())['core']['revision'], "
                    "os.geteuid(), subprocess.check_output(['node', '--version'], text=True).strip())",
                ).split()
                assert (version, core, uid, node) == (args.version, args.core, "10001", "v24.18.0")
                cold = asyncio.run(plan(url, check_tools=True))
                assert len(hits) == len(DEPENDENCIES), hits
                assert asyncio.run(plan(url)) == cold
                assert len(hits) == len(DEPENDENCIES), hits
            with container(args.image, project, state, registry) as (url, _):
                assert asyncio.run(plan(url)) == cold
                assert len(hits) == len(DEPENDENCIES), hits
                assert asyncio.run(plan(url, ttl=0)) == cold
                assert len(hits) == 2 * len(DEPENDENCIES), hits
            assert {path.name: path.read_bytes() for path in project.iterdir()} == before
            assert len(list((state / "registry-metadata-cache").rglob("*.json"))) == len(DEPENDENCIES)
    print(f"Linux {platform}, Core {core}, UID {uid}, Node {node}, metadata GETs cold/warm/recreate/TTL=0: 2/0/0/2")


if __name__ == "__main__":
    main()
