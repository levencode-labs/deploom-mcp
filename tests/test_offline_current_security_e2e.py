"""Run the get_upgrade_plan tool with a local snapshot and a TLS npm bulk endpoint."""
import asyncio
from contextlib import closing
import hashlib
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from deploom_mcp.server import get_upgrade_plan_tool

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from e2e_registry import Handler, PackageStore


class OfflineCoverageEndToEndTests(unittest.TestCase):
    def test_fallback_is_limited_to_missing_pairs_and_keeps_coverage(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        cert = root / "test-cert.pem"
        key = root / "test-key.pem"
        openssl = shutil.which("openssl")
        if not openssl and os.name == "nt":
            openssl = str(Path(shutil.which("git")).resolve().parents[1] / "usr/bin/openssl.exe")
        subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1",
                        "-keyout", str(key), "-out", str(cert)], capture_output=True, check=True)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        packages = ["test-confirmed", "test-unknown", "@inside/test-nexus", "test-overlap"]
        version = "1.0.0"
        ghsa = "GHSA-xxxx-2222-3333"
        bodies = []

        class Mirror(Handler):
            def do_POST(self):
                if self.path != "/-/npm/v1/security/advisories/bulk":
                    self.send_error(404)
                    return
                pairs = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                bodies.append(pairs)
                self._send_json({name: [{"id": 123, "url": "https://github.com/advisories/" + ghsa,
                                         "name": name, "severity": "high", "vulnerable_versions": "=1.0.0"}]
                                 for name in pairs if name in {"@inside/test-nexus", "test-overlap"}})

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Mirror)
        self.addCleanup(httpd.server_close)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        registry = f"https://127.0.0.1:{httpd.server_port}"
        Mirror.store = PackageStore({"packages": {name: {"versions": [{"version": version,
                                    "time": "2020-01-01T00:00:00Z"}]} for name in packages}}, registry)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(httpd.shutdown)
        osv = root / "osv"
        osv.mkdir()
        db = osv / "building.sqlite"
        with closing(sqlite3.connect(db)) as conn:
            conn.execute("CREATE TABLE advisory(name, id, severity, versions, ranges)")
            conn.execute("INSERT INTO advisory VALUES (?, ?, ?, ?, ?)", ("test-overlap", ghsa, "", '["1.0.0"]', "[]"))
            conn.commit()
        digest = hashlib.sha256(db.read_bytes()).hexdigest()
        (osv / "active.json").write_text(json.dumps({"schema": 1, "ecosystem": "npm", "sha256": digest,
                                          "unpacked": db.stat().st_size, "published": int(time.time())}))
        db.rename(osv / (digest + ".sqlite"))
        project = root / "project"
        project.mkdir()
        deps = dict.fromkeys(packages, version)
        (project / "package.json").write_text(json.dumps({"name": "fixture", "version": version, "dependencies": deps}))
        (project / "package-lock.json").write_text(json.dumps({"name": "fixture", "version": version, "lockfileVersion": 3,
            "packages": {"": {"name": "fixture", "version": version, "dependencies": deps},
                         **{"node_modules/" + name: {"version": version} for name in packages}}}))
        env = {"DEPLOOM_MCP_OSV_DIR": str(osv), "DEPLOOM_MCP_PUBLIC_NAMES": "test-confirmed,@inside/test-nexus",
               "DEPLOOM_MCP_PRIVATE_SCOPES": "@inside", "DEPLOOM_MCP_STATE_DIR": str(root / "state"),
               "DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE": "1", "DEPLOOM_MCP_ALLOWED_PROJECT_REGISTRY": registry,
               "DEPLOOM_MCP_REGISTRY_ADVISORY_SOURCE": registry,
               "DEPLOOM_MCP_CORPORATE_ADVISORY_SOURCE": registry, "REQUESTS_CA_BUNDLE": str(cert),
               "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
        for name in ("http_proxy", "https_proxy", "all_proxy"):
            env[name] = env[name.upper()] = "http://127.0.0.1:1"
        with patch.dict(os.environ, env):
            with patch.dict(os.environ, {"DEPLOOM_MCP_CORPORATE_ADVISORY_SOURCE": ""}):
                offline = asyncio.run(get_upgrade_plan_tool(str(project), use_system_proxy=True, deadline_seconds=60))
            self.assertTrue(offline.structured_content["ok"], offline.structured_content.get("error"))
            offline_rows = {row["package"]: row["evidence"]["currentSecurityEvidence"]
                            for row in offline.structured_content["dependencies"]}
            self.assertEqual(offline_rows["@inside/test-nexus"]["reason"], "PRIVATE_SCOPE_EXCLUDED")
            self.assertEqual(offline_rows["test-overlap"]["reason"], "MISSING_SEVERITY")
            self.assertEqual(offline_rows["test-unknown"]["reason"], "PUBLIC_PACKAGE_UNCONFIRMED")
            self.assertEqual(bodies, [])
            result = asyncio.run(get_upgrade_plan_tool(str(project), use_system_proxy=True, deadline_seconds=60))
        payload = result.structured_content
        self.assertTrue(payload["ok"], payload.get("error"))
        self.assertEqual(payload["planningOptions"]["securityEvidenceMode"], "OFFLINE_OSV_NPM")
        self.assertTrue(payload["planningOptions"]["registryFallbackEnabled"])
        rows = {row["package"]: row["evidence"]["currentSecurityEvidence"] for row in payload["dependencies"]}
        self.assertEqual(rows["test-confirmed"], {"status": "ASSESSED", "source": "OFFLINE_OSV_NPM", "findingCount": 0})
        self.assertEqual(rows["test-unknown"]["status"], "UNKNOWN")
        self.assertEqual(rows["test-unknown"]["registryReason"], "COVERAGE_UNCONFIRMED")
        self.assertEqual((rows["@inside/test-nexus"]["source"], rows["@inside/test-nexus"]["findingCount"]), ("REGISTRY_BULK_ADVISORY", 1))
        self.assertEqual((rows["test-overlap"]["source"], rows["test-overlap"]["findingCount"]),
                         ("OFFLINE_OSV_NPM_AND_REGISTRY", 1))
        self.assertEqual({(name, raw) for body in bodies for name, versions in body.items() for raw in versions},
                         {(name, version) for name in packages if name != "test-confirmed"})
