import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit

from semantic_version import Version

from deploom_mcp.migration_release import PUBLIC_NPM, collect_public_transition, _ranges
from deploom_mcp.migration_sources import EvidenceUnavailable, SourceStore, encoded

PACKAGE = "@example/core"
FROM, TO = "1.5.0", "2.1.0"
OLD, NEW = "a" * 40, "b" * 40
INTEGRITY = "sha512-" + base64.b64encode(bytes(64)).decode()


class FrozenRegistryAndGitHub:
    def __init__(self):
        self.calls = []
        self.repository_url = "git+https://github.com/example/monorepo.git"
        self.target_manifest_version = TO
        self.target_package_directory = "packages/core"
        self.with_documents = True
        self.history_name = None
        self.changelog_error = "OFFICIAL_HTTP_404"
        self.release_target = NEW

    def read(self, url, **_kwargs):
        self.calls.append(url)
        parts = urlsplit(url)
        if parts.netloc == "registry.npmjs.org":
            version = parts.path.split("/")[-1]
            return encoded({
                "name": PACKAGE, "version": version, "gitHead": OLD if version == FROM else NEW,
                "dist": {"integrity": INTEGRITY, "tarball": f"{PUBLIC_NPM}/{PACKAGE}/-/core-{version}.tgz"},
                "repository": {"url": self.repository_url,
                               "directory": "packages/core" if version == FROM else self.target_package_directory},
                "engines": {"node": ">=18" if version == FROM else ">=22"},
            })
        if parts.netloc == "api.github.com":
            tag = unquote(parts.path.rsplit("/", 1)[-1])
            if "/git/ref/" in parts.path:
                return encoded({"ref": f"refs/tags/{tag}", "object": {"type": "commit", "sha": self.release_target}})
            return encoded({"tag_name": tag, "prerelease": False, "draft": False,
                            "body": "Breaking: remove loadSync."})
        if parts.path.endswith("/packages/core/package.json"):
            return encoded({"name": PACKAGE, "version": FROM if OLD in parts.path else self.target_manifest_version})
        if self.with_documents and parts.path.endswith("/packages/core/CHANGELOG.md"):
            return b"## 2.2.0\nBreaking: future change.\n## 2.1.0\nBreaking: remove loadSync.\n## 2.0.0\nNode.js >=22 required.\n## 1.5.0\nBreaking: before migration.\n"
        if self.with_documents and parts.path.endswith("/packages/core/MIGRATION.md"):
            return b"# Migration guide\nCodemod: rename loadSync.\n"
        if self.history_name and parts.path.endswith(f"/packages/core/{self.history_name}"):
            return (b"2.2.0 / 2026-08-03\n=====\nBreaking: future change.\n\n"
                    b"2.1.0 / 2026-07-12\n=====\n## Breaking changes\nRemoved loadSync.\n\n"
                    b"2.0.0 / 2026-06-07\n=====\nNode.js >=22 required.\n\n"
                    b"1.5.0 / 2025-12-03\n=====\nBreaking: before migration.\n")
        if parts.path.endswith("/packages/core/CHANGELOG.md"):
            raise EvidenceUnavailable(self.changelog_error)
        raise EvidenceUnavailable("OFFICIAL_HTTP_404")


class PublicReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.http = FrozenRegistryAndGitHub()
        self.clock = 100_000

    def collect(self, *, offline=False, integrity=INTEGRITY):
        store = SourceStore(self.root, deadline=time.monotonic() + 5, transport=self.http,
                            offline=offline, clock=lambda: self.clock)
        return collect_public_transition(store, PACKAGE, FROM, TO, integrity), store.metrics

    def test_monorepo_range_and_warm_offline_replay(self):
        cold, metrics = self.collect()
        self.assertEqual(cold["status"], "AVAILABLE")
        self.assertEqual(cold["packageDirectory"], "packages/core")
        self.assertEqual(cold["requirements"]["to"]["engines"]["node"], ">=22")
        self.assertEqual(set(cold["documents"][0]["matchedVersions"]), {"2.1.0", "2.0.0"})
        self.assertNotIn("future change", json.dumps(cold))
        self.assertNotIn("before migration", json.dumps(cold))
        self.assertTrue(all(s["transport"] == "ENGINEERING_CONTROL_TRANSPORT" for s in cold["sources"]))
        self.assertFalse(cold["rangeComplete"])
        self.assertFalse(cold["tarballSourceAttested"])
        self.assertEqual(metrics["httpTransactions"], 6)
        warm, metrics = self.collect(offline=True)
        self.assertEqual(cold, warm)
        self.assertEqual(metrics["httpTransactions"], 0)

    def test_rejects_wrong_package_version_before_reading_changelog(self):
        self.http.target_manifest_version = "2.0.0"
        result, _ = self.collect()
        self.assertEqual(result["reason"], "PUBLISHED_SOURCE_MANIFEST_MISMATCH")
        self.assertFalse(result["sources"])
        self.assertFalse(any(url.endswith("CHANGELOG.md") for url in self.http.calls))

    def test_repository_directory_change_is_not_guessed(self):
        self.http.target_package_directory = "packages/sibling"
        result, _ = self.collect()
        self.assertEqual(result["reason"], "PUBLISHED_REPOSITORY_CHANGED")
        self.assertEqual(len(self.http.calls), 2)

    def test_registry_hint_and_baseline_are_checked_before_github(self):
        self.http.repository_url = "https://github.com@example.test/owner/repo"
        result, _ = self.collect()
        self.assertEqual(result["reason"], "PUBLISHED_REPOSITORY_UNVERIFIED")
        self.assertEqual(len(self.http.calls), 1)
        self.http.calls.clear()
        result, _ = self.collect(integrity=None)
        self.assertEqual(result["reason"], "PUBLIC_BASELINE_UNPROVEN")
        self.assertFalse(self.http.calls)
        mismatch = "sha512-" + base64.b64encode(b"x" * 64).decode()
        result, _ = self.collect(integrity=mismatch)
        self.assertEqual(result["reason"], "PUBLIC_NPM_ARTIFACT_MISMATCH")
        self.assertFalse(self.http.calls)

    def test_independent_tag_fallback_requires_the_published_commit(self):
        self.http.with_documents = False
        result, _ = self.collect()
        self.assertEqual(result["documents"][0]["source"]["kind"], "GITHUB_RELEASE")
        self.assertIn("%40example%2Fcore%402.1.0", result["documents"][0]["source"]["url"])
        self.assertEqual(result["documents"][0]["source"]["resolvedCommit"], NEW)
        self.clock += 86401
        self.http.release_target = OLD
        result, _ = self.collect()
        self.assertEqual(result["documents"], [])
        self.assertIn("PIN_DRIFT", {gap["reason"] for gap in result["unknowns"]})

    def test_range_excludes_later_unreleased_and_prerelease_sections(self):
        for heading in ("## Unreleased", "## 3.0.0-rc.1"):
            with self.subTest(heading=heading):
                versions, excerpts = _ranges(
                    "## 2.1.0\n### Migration\nRemoved old option.\n" + heading + "\nBreaking: future release.",
                    Version("1.5.0"), Version("2.1.0"),
                )
                self.assertEqual(versions, ["2.1.0"])
                self.assertIn("Removed old option.", excerpts)
                self.assertFalse(any("future" in line for line in excerpts))

    def test_fenced_release_heading_is_not_a_version_section(self):
        text = "## 2.0.0\n~~~markdown\n## 2.1.0\nBreaking: example only.\n~~~\n"
        self.assertEqual(_ranges(text, Version("2.0.0"), Version("2.1.0")), ([], []))

    def test_history_fallback_reads_underlined_version_sections_and_replays_offline(self):
        parent = self.root
        for name, calls in (("History.md", 7), ("HISTORY.md", 8)):
            with self.subTest(name=name):
                self.root = parent / f"fixture-{calls}"
                self.http.with_documents = False
                self.http.history_name = name
                cold, metrics = self.collect()
                self.assertEqual(metrics["httpTransactions"], calls)
                row, = cold["documents"]
                self.assertEqual(row["scope"], "VERSION_SECTIONS")
                self.assertTrue(row["source"]["url"].endswith("/packages/core/" + name))
                self.assertEqual(row["source"]["resolvedCommit"], NEW)
                self.assertEqual(row["matchedVersions"], ["2.1.0", "2.0.0"])
                self.assertIn("Removed loadSync.", row["excerpts"])
                self.assertIn("Node.js >=22 required.", row["excerpts"])
                self.assertNotIn("future change", json.dumps(row))
                self.assertNotIn("before migration", json.dumps(row))
                warm, metrics = self.collect(offline=True)
                self.assertEqual(warm["documents"], cold["documents"])
                self.assertEqual(warm["sources"], cold["sources"])
                self.assertEqual(metrics["httpTransactions"], 0)

    def test_history_discovery_stops_on_a_changelog_redirect(self):
        self.http.with_documents = False
        self.http.history_name = "History.md"
        self.http.changelog_error = "OFFICIAL_REDIRECT_BLOCKED"
        result, metrics = self.collect()
        self.assertEqual(result["documents"], [])
        self.assertEqual(metrics["httpTransactions"], 5)
        self.assertFalse(any(url.endswith("History.md") for url in self.http.calls))

    def test_version_range_keeps_actionable_replacement_heading(self):
        text = (
            "## 4.5.3\n### Use `futureOption` instead of `rollupOptions`\n"
            "## 4.5.2\n"
            "### Use `optimizeDeps.rollupOptions` instead of `optimizeDeps.esbuildOptions`\n"
            "The old option is deprecated.\n"
            "~~~markdown\n### Use `example` instead of `oldExample`\n~~~\n"
            "## 4.5.1\n### Use `oldOption` instead of `legacyOption`\n"
        )
        versions, excerpts = _ranges(text, Version("4.5.1"), Version("4.5.2"))
        self.assertEqual(versions, ["4.5.2"])
        self.assertEqual(excerpts, [
            "Use `optimizeDeps.rollupOptions` instead of `optimizeDeps.esbuildOptions`",
            "The old option is deprecated.",
        ])

    def test_cold_offline_replays_no_requests(self):
        result, metrics = self.collect(offline=True)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(metrics["httpTransactions"], 0)
        self.assertFalse(self.http.calls)
