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

from deploom_mcp import blueprint
from deploom_mcp.server import get_upgrade_plan_tool

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from e2e_registry import Handler, PackageStore


class OfflineCoverageEndToEndTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cert = self.root / "test-cert.pem"
        key = self.root / "test-key.pem"
        openssl = shutil.which("openssl")
        if not openssl and os.name == "nt":
            openssl = str(Path(shutil.which("git")).resolve().parents[1] / "usr/bin/openssl.exe")
        subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=localhost", "-addext",
                        "subjectAltName=IP:127.0.0.1,DNS:registry.npmjs.org,DNS:mirror.test.invalid",
                        "-keyout", str(key), "-out", str(self.cert)], capture_output=True, check=True)
        self.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.tls.load_cert_chain(self.cert, key)

    def start_registry(self, handler):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.addCleanup(httpd.server_close)
        httpd.socket = self.tls.wrap_socket(httpd.socket, server_side=True)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(httpd.shutdown)
        return httpd.server_port

    def snapshot(self, records=()):
        osv = self.root / "osv"
        osv.mkdir()
        db = osv / "building.sqlite"
        with closing(sqlite3.connect(db)) as conn:
            conn.execute("CREATE TABLE advisory(name, id, severity, versions, ranges)")
            conn.executemany("INSERT INTO advisory VALUES (?, ?, ?, ?, ?)", records)
            conn.commit()
        digest = hashlib.sha256(db.read_bytes()).hexdigest()
        (osv / "active.json").write_text(json.dumps({"schema": 1, "ecosystem": "npm", "sha256": digest,
                                          "unpacked": db.stat().st_size, "published": int(time.time())}))
        db.rename(osv / (digest + ".sqlite"))
        return osv

    def test_fallback_is_limited_to_missing_pairs_and_keeps_coverage(self):
        root, cert = self.root, self.cert
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

        registry = f"https://127.0.0.1:{self.start_registry(Mirror)}"
        Mirror.store = PackageStore({"packages": {name: {"versions": [{"version": version,
                                    "time": "2020-01-01T00:00:00Z"}]} for name in packages}}, registry)
        osv = self.snapshot([("test-overlap", ghsa, "", '["1.0.0"]', "[]")])
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

    def test_public_metadata_covers_exact_pairs_without_advisory_egress(self):
        version = "1.0.0"
        public = "https://registry.npmjs.org"
        packages = ["fixture-clean", "fixture-wrong-name", "fixture-foreign-lock"]
        store = PackageStore({"packages": {name: {"versions": [{"version": version,
                             "time": "2020-01-01T00:00:00Z"}]} for name in packages}}, public)
        requests_seen = []

        class PublicRegistry(Handler):
            def do_GET(self):
                requests_seen.append((self.headers["Host"], self.path))
                name = self.path.lstrip("/")
                metadata = store.metadata(name)
                if metadata is None:
                    return self._send_json({}, 404)
                if name == "fixture-wrong-name":
                    metadata["versions"][version]["name"] = "fixture-other"
                self._send_json(metadata)

            def do_POST(self):
                requests_seen.append((self.headers["Host"], self.path))
                self.send_error(405)

        port = self.start_registry(PublicRegistry)
        project = self.root / "project"
        project.mkdir()
        deps = dict.fromkeys(packages, version)
        manifest = {"name": "fixture-project", "version": version, "dependencies": deps}
        (project / "package.json").write_text(json.dumps(manifest))
        entries = {"": manifest}
        for name in packages:
            dist = store.metadata(name)["versions"][version]["dist"]
            entries["node_modules/" + name] = {"version": version, "resolved": dist["tarball"],
                                                "integrity": dist["integrity"]}
        foreign = entries["node_modules/fixture-foreign-lock"]
        foreign["resolved"] = foreign["resolved"].replace(public, "https://other.test.invalid")
        (project / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": entries}))
        denied = self.root / "blocked-requests"
        fixture = Path(__file__).parent / "fixtures" / "loopback_registry_draft.py"
        ordinary = {key: value for key, value in os.environ.items() if not key.startswith("DEPLOOM_")}
        ordinary.update(DEPLOOM_MCP_OSV_DIR=str(self.snapshot()), DEPLOOM_MCP_STATE_DIR=str(self.root / "state"))
        original = blueprint._prepare_argv_full

        def isolated(*args):
            return [str(fixture.resolve()), str(self.cert), str(port), str(denied), *original(*args)]

        def plan(registry=None):
            result = asyncio.run(get_upgrade_plan_tool(str(project), registry=registry,
                                                       use_system_proxy=False, deadline_seconds=45))
            self.assertTrue(result.structured_content["ok"], result.structured_content.get("error"))
            return {row["package"]: row["evidence"]["currentSecurityEvidence"]
                    for row in result.structured_content["dependencies"]}

        with patch.dict(os.environ, ordinary, clear=True), patch.object(blueprint, "_prepare_argv_full", isolated):
            cold = plan()
            self.assertEqual(cold["fixture-clean"], {"status": "ASSESSED", "source": "OFFLINE_OSV_NPM",
                             "findingCount": 0, "publicIdentitySource": "NPM_PUBLIC_PACKUMENT"})
            self.assertEqual(cold["fixture-wrong-name"]["reason"], "PUBLIC_PACKAGE_UNCONFIRMED")
            self.assertEqual(cold["fixture-foreign-lock"]["reason"], "PUBLIC_PACKAGE_UNCONFIRMED")
            self.assertCountEqual(requests_seen, [("registry.npmjs.org", "/" + name) for name in packages])
            requests_seen.clear()
            self.assertEqual(plan(), cold)
            self.assertEqual(requests_seen, [])
            mirror = plan("https://mirror.test.invalid")
            self.assertEqual(mirror["fixture-clean"]["reason"], "PUBLIC_PACKAGE_UNCONFIRMED")
            self.assertCountEqual(requests_seen, [("mirror.test.invalid", "/" + name) for name in packages])
            requests_seen.clear()
            with patch.dict(os.environ, {"DEPLOOM_MCP_PRIVATE_SCOPES": "@inside", "DEPLOOM_MCP_OSV_DIR": ""}):
                manifest["dependencies"]["@inside/fixture-private"] = version
                (project / "package.json").write_text(json.dumps(manifest))
                blocked = asyncio.run(get_upgrade_plan_tool(str(project))).structured_content
            self.assertEqual(blocked.get("error", {}).get("code"), "private-metadata-registry-required")
            self.assertEqual(requests_seen, [])
        self.assertFalse(denied.exists(), denied.read_text() if denied.exists() else "")
