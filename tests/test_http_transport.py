"""Exercise the HTTP entry point and the default stdio contract."""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from deploom_mcp import server as module


class TransportTests(unittest.TestCase):
    def test_default_stdio(self):
        with patch.object(sys, "argv", ["deploom-mcp"]), patch.object(module.server, "run") as run:
            self.assertEqual(module.main(), 0)
            run.assert_called_once_with(transport="stdio")

    def test_http_security(self):
        for extra, expected in [([], ["127.0.0.1:*", "localhost:*", "[::1]:*"]), (["--allowed-host", "mcp.example.com"], ["mcp.example.com"])]:
            with self.subTest(extra=extra), patch.object(sys, "argv", ["deploom-mcp", "--transport", "streamable-http", "--host", "0.0.0.0", *extra]), patch.object(module.server, "run") as run:
                module.main()
                settings = run.call_args.kwargs["transport_security"]
                self.assertTrue(settings.enable_dns_rebinding_protection)
                self.assertEqual(settings.allowed_hosts, expected)

    @contextmanager
    def http_server(self, *extra, settings=None):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {key: value for key, value in os.environ.items() if not key.startswith("DEPLOOM_")}
        env.update(settings or {})
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        proc = subprocess.Popen(
            [sys.executable, "-m", "deploom_mcp", "--transport", "streamable-http", "--port", str(port), *extra],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(100):
                if proc.poll() is not None:
                    self.fail("HTTP server exited before becoming ready")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                self.fail("HTTP server did not start")

            yield f"http://127.0.0.1:{port}/mcp"
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    def test_real_http_protocol(self):
        with self.http_server() as url:
            async def exercise():
                async with streamable_http_client(url) as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        self.assertIn("get_upgrade_plan", [tool.name for tool in tools.tools])
                        result = await session.call_tool("get_upgrade_plan", {"project_path": ""})
                        self.assertTrue(result.is_error)
            asyncio.run(exercise())

    def test_pilot_is_bound_to_a_single_project_and_flat_tools(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw).resolve()
            project = base / "project"
            project.mkdir()
            package = {"name": "pilot-fixture", "version": "1.0.0"}
            manifest = project / "package.json"
            manifest.write_text(json.dumps(package), encoding="utf-8")
            lock = project / "package-lock.json"
            lock.write_text(json.dumps({**package, "lockfileVersion": 3, "packages": {"": package}}), encoding="utf-8")
            snapshot = {path.name: path.read_bytes() for path in project.iterdir()}
            state = base / "state"
            blocked_output = base / "denied"
            settings = {
                "DEPLOOM_MCP_STATE_DIR": str(state),
                "DEPLOOM_MCP_ALLOWED_PROJECT_REGISTRY": "https://registry.example.invalid",
                "DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE": "1",
            }

            async def exercise(url):
                async with streamable_http_client(url) as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        self.assertEqual({tool.name for tool in tools.tools},
                                         {"get_project_capabilities", "get_upgrade_plan"})
                        result = await session.call_tool("get_project_capabilities", {"project_path": str(project)})
                        self.assertFalse(result.is_error)
                        self.assertEqual([route["tool"] for route in result.structured_content["routes"]],
                                         ["get_upgrade_plan"])
                        valid = {"project_path": str(project)}
                        for tool, arguments, code in (
                            ("get_goal_plan", {**valid, "goal": {}}, "pilot-tool-disabled"),
                            ("get_project_capabilities", {"project_path": str(base)}, "pilot-project-denied"),
                            ("get_upgrade_plan", {"project_path": str(base)}, "pilot-project-denied"),
                            ("get_upgrade_plan", {**valid, "workspace_request": {}}, "pilot-options-denied"),
                            ("get_upgrade_plan", {**valid, "output_dir": str(blocked_output)}, "pilot-options-denied"),
                            ("get_upgrade_plan", {**valid, "registry": "https://other.invalid"}, "pilot-options-denied"),
                            ("get_upgrade_plan", {**valid, "use_system_proxy": True}, "pilot-options-denied"),
                            ("get_upgrade_plan", {**valid, "registry_metadata_ttl": 3601}, "pilot-options-denied"),
                        ):
                            with self.subTest(tool=tool, code=code):
                                result = await session.call_tool(tool, arguments)
                                self.assertTrue(result.is_error)
                                self.assertEqual(result.structured_content["error"]["code"], code)
                        self.assertEqual(list(state.iterdir()), [])
                        result = await session.call_tool("get_upgrade_plan", valid)
                        self.assertFalse(result.is_error, result.structured_content)
                        self.assertEqual(result.structured_content["status"]["authority"], "PLANNING_ONLY")
                        artifacts = set((state / "artifacts").iterdir())

                        lock.write_text(json.dumps({**package, "lockfileVersion": 2, "packages": {"": package}}),
                                        encoding="utf-8")
                        result = await session.call_tool("get_upgrade_plan", valid)
                        self.assertTrue(result.is_error)
                        self.assertEqual(result.structured_content["error"]["code"], "pilot-input-changed")
                        self.assertEqual(set((state / "artifacts").iterdir()), artifacts)
                        lock.write_bytes(snapshot[lock.name])

                        with self.subTest("symlinked manifest is refused by both tools"):
                            outside = base / "outside.json"
                            outside.write_text(json.dumps({"name": "outside-marker", "version": "1.0.0"}), encoding="utf-8")
                            manifest.unlink()
                            try:
                                try:
                                    manifest.symlink_to(outside)
                                except OSError as exc:
                                    if getattr(exc, "winerror", None) != 1314:
                                        raise
                                    self.skipTest("Windows symbolic link privilege is unavailable")
                                for tool in ("get_project_capabilities", "get_upgrade_plan"):
                                    result = await session.call_tool(tool, valid)
                                    self.assertTrue(result.is_error)
                                    self.assertNotIn("outside-marker", json.dumps(result.structured_content))
                                self.assertEqual(set((state / "artifacts").iterdir()), artifacts)
                            finally:
                                manifest.unlink(missing_ok=True)
                                manifest.write_bytes(snapshot[manifest.name])

            with self.http_server("--pilot-project", str(project), settings=settings) as url:
                asyncio.run(exercise(url))
            self.assertFalse(blocked_output.exists())
            self.assertEqual({path.name: path.read_bytes() for path in project.iterdir()}, snapshot)


if __name__ == "__main__":
    unittest.main()
