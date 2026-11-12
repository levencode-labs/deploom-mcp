import asyncio
import base64
from hashlib import sha256
from contextlib import redirect_stdout
from io import StringIO
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

import test_workspace_inventory as fixture_module
from deploom_mcp import workspace_edits
from deploom_mcp.distribution import resolve_core_dir
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.workspace_edits import _manifest_patch, apply_workspace_patch, check_workspace_patch, plan_workspace_edits
from deploom_mcp.workspace_goal_request import validate_goal_request
from deploom_mcp.workspace_inventory import observe_npm_workspace
from deploom_mcp.workspace_plan_snapshot import plan_workspace_snapshot
from deploom_mcp.workspace_upgrade import get_workspace_upgrade_plan

REGISTRY = 'https://registry.example.invalid'


class WorkspaceGoalRequestTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.WorkspaceInventoryTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.lock['packages']['node_modules/typescript'] = {'version': '5.0.0'}
        self.fixture.save()
        self.root = self.fixture.root
        sys.path.insert(0, str(resolve_core_dir()))
        self.addCleanup(sys.path.pop, 0)
        self.request = {'schemaVersion': 3, 'goalKind': 'TARGETED_UPGRADE', 'registry': REGISTRY, 'scope': [
            {'selector': {'name': 'kleur', 'owner': 'packages/a'}, 'approvedLines': [3], 'exactTarget': '3.0.1'},
            {'selector': {'name': 'typescript'}, 'approvedLines': [5]}]}

    def run_plan(self, request=None, before_worker=None, metadata_source=None):
        from dependency_live_roadmap_generator import LiveDataClient
        def metadata(name):
            versions = [f'5.0.{i}' for i in range(40)] if name == 'typescript' else ['3.0.0', '3.0.1', '4.0.0']
            return {'versions': {v: {'name': name, 'version': v, 'dist': {
                'tarball': f'{REGISTRY}/{name}/-/{name}-{v}.tgz'}} for v in versions}}, True
        async def worker(project_path, snapshot, **kwargs):
            if before_worker:
                before_worker()
            try:
                plan = plan_workspace_snapshot(Path(project_path), snapshot, **kwargs)
                return json.loads(json.dumps({'ok': True, 'plan': plan}))
            except GoalContractError as exc:
                return {'ok': False, 'error': {'code': exc.code}}
        inventory = {'ok': True, 'inventory': observe_npm_workspace(self.root)}
        with patch('deploom_mcp.workspace_goal_request.observe_workspace_inventory_v1',
                   new=AsyncMock(return_value=inventory)), patch(
            'deploom_mcp.workspace_upgrade.plan_workspace_snapshot_v1', side_effect=worker), patch.dict(
            os.environ, {'DEPLOOM_WORKSPACE_POLICY_REGISTRY': REGISTRY}, clear=True), patch.object(
            LiveDataClient, '_fetch_npm_metadata_result', side_effect=metadata_source or metadata), patch(
            'workspace_npm_preflight.observe_workspace_peer_overrides', return_value={}):
            request = request or self.request
            planner = plan_workspace_edits if request.get("responseMode") == "EDITS" else get_workspace_upgrade_plan
            return asyncio.run(planner(str(self.root), request))

    def test_manifest_patch_applies_exact_target_and_refuses_drift(self):
        manifest = self.root / "packages/a/package.json"
        original = manifest.read_bytes()
        request = {**self.request, "responseMode": "EDITS"}
        with tempfile.TemporaryDirectory() as directory:
            with patch("deploom_mcp.workspace_edits.configured_state_root", return_value=Path(directory)):
                result = self.run_plan(request)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["changeCount"], 1)
            self.assertEqual(result["installation"]["status"], "NOT_RUN")
            self.assertFalse(result["executionReady"])
            self.assertEqual(manifest.read_bytes(), original)
            artifact = Path(result["patch"]["path"])
            check_workspace_patch(self.root, result["inventoryDigest"], artifact, result["patch"]["sha256"])
            with self.assertRaisesRegex(GoalContractError, "WORKSPACE_PATCH_DIGEST_MISMATCH"):
                check_workspace_patch(self.root, result["inventoryDigest"], artifact, "0" * 64)
            with self.assertRaisesRegex(GoalContractError, "WORKSPACE_PATCH_DIGEST_MISMATCH"):
                apply_workspace_patch(self.root, result["inventoryDigest"], artifact, "0" * 64)
            self.assertEqual(manifest.read_bytes(), original)
            subprocess.run(["git", "init", "-q"], cwd=self.root.parent, check=True)
            apply_workspace_patch(self.root, result["inventoryDigest"], artifact, result["patch"]["sha256"])
            expected = json.loads(original)
            expected["dependencies"]["kleur"] = "3.0.1"
            self.assertEqual(json.loads(manifest.read_bytes()), expected)
            with self.assertRaisesRegex(GoalContractError, "WORKSPACE_MANIFEST_LOCK_MISMATCH"):
                apply_workspace_patch(self.root, result["inventoryDigest"], artifact, result["patch"]["sha256"])

    def test_apply_preserves_manifest_bytes_under_git_eol_attributes(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        (self.root / ".gitattributes").write_bytes(
            b"package.json text eol=crlf\npackages/a/package.json text eol=lf\n"
        )
        manifests = {
            "package.json": ("devDependencies", "typescript", "5.0.0", "5.0.1", b"\n", b""),
            "packages/a/package.json": ("dependencies", "kleur", "^3.0.0", "3.0.1", b"\r\n", b"\xef\xbb\xbf"),
        }
        chunks, originals, expected = [], {}, {}
        for name, (section, dependency, before, after, eol, bom) in manifests.items():
            manifest = self.root / name
            raw = bom + json.dumps(json.loads(manifest.read_bytes()), indent=2).encode().replace(b"\n", eol)
            manifest.write_bytes(raw)
            originals[manifest] = raw
            chunks.append(_manifest_patch(name, raw, [{"section": section, "name": dependency,
                                                       "fromSpec": before, "toVersion": after}]))
            expected[manifest] = raw.replace(json.dumps(before).encode(), json.dumps(after).encode(), 1)
        inventory = observe_npm_workspace(self.root)["inventoryDigest"]
        patch_file = self.root.parent / (self.root.name + ".patch")
        self.addCleanup(patch_file.unlink, missing_ok=True)
        patch_bytes = b"".join(chunks)
        patch_file.write_bytes(patch_bytes)
        digest = sha256(patch_bytes).hexdigest()
        replace_manifest = workspace_edits._replace_manifest

        def fail_second_manifest(manifest, raw):
            if manifest == self.root / "packages/a/package.json":
                raise PermissionError("injected replacement failure")
            replace_manifest(manifest, raw)

        with patch.object(workspace_edits, "_replace_manifest", side_effect=fail_second_manifest):
            with self.assertRaisesRegex(PermissionError, "injected replacement failure"):
                apply_workspace_patch(self.root, inventory, patch_file, digest)
        for manifest, raw in originals.items():
            self.assertEqual(manifest.read_bytes(), raw)

        apply_workspace_patch(self.root, inventory, patch_file, digest)
        for manifest, raw in expected.items():
            self.assertEqual(manifest.read_bytes(), raw)

    def test_root_peer_conflicts_reach_plan_and_details_without_another_probe(self):
        from deploom_mcp.workspace_plan_details import get_workspace_plan_details

        target = {"selector": {"owner": ".", "section": "devDependencies", "name": "typescript"},
                  "approvedLines": [5], "exactTarget": "5.0.1"}
        request = {**self.request, "scope": [target], "preflight": {"reauthor": None, "candidateLimit": 1}}
        receipt = {"status": "RESOLUTION_FAILED", "code": "HELD_LOCK_PROVIDER_CHANGED",
                   "phase": "BASELINE_ROOT_PEER_CHECK", "attempts": [], "conflictCount": 1,
                   "heldConflicts": [{"path": "node_modules/@scope/consumer", "dependency": "typescript",
                                      "beforeVersion": "5.0.0", "requestedVersion": "5.0.1"}]}
        with patch("deploom_mcp.workspace_plan_snapshot._preflight", return_value=receipt) as preflight:
            result = self.run_plan(request, before_worker=lambda: os.environ.update(DEPLOOM_WORKSPACE_NPM_REGISTRY=REGISTRY))
        self.assertTrue(result["ok"], result)
        preflight.assert_called_once()
        expected = {"conflictCount": 1, "conflicts": [{"consumer": "@scope/consumer",
                    "dependency": "typescript", "beforeVersion": "5.0.0", "requestedVersion": "5.0.1"}]}
        self.assertEqual(result["packageManagerValidation"]["heldRootPeers"], expected)
        self.assertEqual(result["action"]["kind"], "INSPECT_BLOCKER")
        details = get_workspace_plan_details(
            plan_id=result["details"]["planId"], request_id=result["requestId"], candidate_id=result["candidateId"],
            snapshot_sha256=result["details"]["snapshotSha256"], owner=".", section="devDependencies",
        )
        self.assertTrue(details["ok"], details)
        self.assertEqual(details["packageManagerValidation"]["heldRootPeers"], expected)
        self.assertEqual(details["candidateReceipts"][0]["heldRootPeers"], expected)
        self.assertEqual(details["candidateReceipts"][0]["commands"], [])

    def test_details_reports_all_held_lock_dependency_names_from_one_attempt(self):
        from deploom_mcp.workspace_plan_details import get_workspace_plan_details

        target = {"selector": {"owner": ".", "section": "devDependencies", "name": "typescript"},
                  "approvedLines": [5], "exactTarget": "5.0.1"}
        request = {**self.request, "scope": [target], "preflight": {"reauthor": None}}
        receipt = {"status": "RESOLUTION_FAILED", "code": "LOCK_UNRELATED_NODE_DELTA",
                   "lockDelta": {"status": "RESOLUTION_FAILED", "code": "LOCK_UNRELATED_NODE_DELTA",
                                 "heldConflict": {"code": "HELD_LOCK_PROVIDER_CHANGED", "heldConflictCount": 3,
                                                  "heldConflicts": [{"dependency": "ajv"}, {"dependency": "ajv"},
                                                                    {"dependency": "terser"}]}}}
        with patch("deploom_mcp.workspace_plan_snapshot._preflight", return_value=receipt) as native:
            result = self.run_plan(request, before_worker=lambda: os.environ.update(DEPLOOM_WORKSPACE_NPM_REGISTRY=REGISTRY))
        self.assertTrue(result["ok"], result)
        native.assert_called_once()
        details = get_workspace_plan_details(
            plan_id=result["details"]["planId"], request_id=result["requestId"], candidate_id=result["candidateId"],
            snapshot_sha256=result["details"]["snapshotSha256"], owner=".", section="devDependencies",
        )
        self.assertTrue(details["ok"], details)
        for row in (details["packageManagerValidation"], details["candidateReceipts"][0]):
            self.assertEqual(row["lockDelta"]["heldConflictCount"], 3)
            self.assertEqual(row["lockDelta"]["heldDependencies"], ["ajv", "terser"])

    def test_held_root_version_domain_skips_metadata_and_native_preflight(self):
        target = {"selector": {"owner": ".", "section": "devDependencies", "name": "typescript"},
                  "approvedLines": [5], "exactTarget": "5.0.1"}
        request = {**self.request, "scope": [target], "preflight": {"reauthor": None}}
        conflict = {"path": "node_modules/plugin", "dependency": "typescript", "beforeVersion": "5.0.0",
                    "requestedVersion": "5.0.1", "requestedVersions": ["5.0.1"]}

        def unexpected_metadata(_):
            self.fail("A domain blocked by the baseline required registry metadata")

        with (patch("workspace_npm_lock_delta.forced_root_peer_domain_conflicts", create=True,
                    return_value=[conflict]) as checker,
              patch("deploom_mcp.workspace_plan_snapshot._preflight") as native):
            result = self.run_plan(request, metadata_source=unexpected_metadata,
                                   before_worker=lambda: os.environ.update(DEPLOOM_WORKSPACE_NPM_REGISTRY=REGISTRY))
        self.assertTrue(result["ok"], result)
        checker.assert_called_once()
        self.assertEqual(checker.call_args.args[1], {"typescript": ("5.0.1",)})
        native.assert_not_called()
        self.assertEqual(result["constraintModel"]["reason"], "WORKSPACE_HELD_ROOT_PEER_DOMAIN_BLOCKED")
        self.assertEqual(result["action"], {"kind": "INSPECT_BLOCKER", "code": "HELD_LOCK_PROVIDER_CHANGED"})
        validation = result["packageManagerValidation"]
        self.assertEqual(validation["heldRootPeers"]["conflicts"][0]["requestedVersions"], ["5.0.1"])
        self.assertEqual(validation["search"]["attemptCount"], 0)
        self.assertEqual(result["installation"]["status"], "NOT_RUN")

    def test_native_intent_returns_held_root_peers_before_writing_a_patch(self):
        lock = self.fixture.lock["packages"]["node_modules/typescript"]
        lock.update(resolved=REGISTRY + "/typescript/-/typescript-5.0.0.tgz",
                    integrity="sha512-" + base64.b64encode(bytes(64)).decode())
        self.fixture.save()
        request = {**self.request, "responseMode": "EDITS", "editsStrategy": "NATIVE_INTENT", "scope": [
            {"selector": {"owner": ".", "section": "devDependencies", "name": "typescript"},
             "approvedLines": [5], "exactTarget": "5.0.1"},
        ]}
        conflicts = [{"path": "node_modules/plugin", "dependency": "typescript",
                      "beforeVersion": "5.0.0", "requestedVersion": "5.0.1"}]
        with patch.dict(os.environ, {"DEPLOOM_WORKSPACE_NPM_REGISTRY": REGISTRY}), patch(
                "workspace_npm_lock_delta.forced_root_peer_conflicts", create=True, return_value=conflicts) as checker, patch(
                "deploom_mcp.workspace_edits._save_patch") as save_patch:
            result = asyncio.run(plan_workspace_edits(str(self.root), request))
        save_patch.assert_not_called()
        checker.assert_called_once()
        self.assertEqual(checker.call_args.args[1], {"typescript": "5.0.1"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "HELD_LOCK_PROVIDER_CHANGED")
        self.assertEqual(result["error"]["heldRootPeers"]["conflicts"][0]["consumer"], "plugin")
        self.assertNotIn("patch", result)

    def test_native_intent_uses_bound_lock_without_calling_registry_or_solver(self):
        manifest = self.root / "packages/a/package.json"
        original = manifest.read_bytes()
        target = self.request["scope"][0]
        request = {**self.request, "responseMode": "EDITS", "editsStrategy": "NATIVE_INTENT",
                   "scope": [{**target, "selector": {**target["selector"], "section": "dependencies"}}]}
        lock = self.fixture.lock["packages"]["node_modules/kleur"]
        lock.update(resolved=REGISTRY + "/kleur/-/kleur-3.0.0.tgz",
                    integrity="sha512-" + base64.b64encode(bytes(64)).decode())
        self.fixture.save()
        with tempfile.TemporaryDirectory() as directory, patch.dict(
                os.environ, {"DEPLOOM_WORKSPACE_NPM_REGISTRY": REGISTRY}), patch(
                "deploom_mcp.workspace_edits.configured_state_root", return_value=Path(directory)), patch(
                "deploom_mcp.workspace_upgrade.get_workspace_upgrade_plan", new=AsyncMock()) as solver:
            result = asyncio.run(plan_workspace_edits(str(self.root), request))
            solver.assert_not_awaited()
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["authority"], "MANIFEST_INTENT_ONLY")
            self.assertEqual(result["jointAssignment"], "NOT_COMPUTED")
            self.assertEqual(result["packageManagerValidation"]["status"], "REQUIRED")
            self.assertNotIn("constraintModel", result)
            self.assertEqual(manifest.read_bytes(), original)
            artifact = Path(result["patch"]["path"])
            self.assertIn(b'"3.0.1"', artifact.read_bytes())
            blocked = asyncio.run(plan_workspace_edits(
                str(self.root), {**request, "registry": "https://unapproved.invalid"}))
            self.assertEqual(blocked["error"]["code"], "WORKSPACE_REQUEST_REGISTRY_MISMATCH")
            lock["resolved"] = "https://unapproved.invalid/kleur/-/kleur-3.0.0.tgz"
            self.fixture.save()
            blocked = asyncio.run(plan_workspace_edits(str(self.root), request))
            self.assertEqual(blocked["error"]["code"], "NATIVE_INTENT_BASELINE_ARTIFACT_UNPROVEN")
            self.assertEqual([path.resolve() for path in Path(directory).glob("*.patch")], [artifact.resolve()])

    def test_native_intent_plans_and_applies_a_root_only_npm_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = root / "project"
            project.mkdir()
            manifest = {"name": "single-package", "version": "1.0.0", "devDependencies": {"kleur": "^3.0.0"}}
            lock = {"lockfileVersion": 3, "packages": {"": manifest, "node_modules/kleur": {
                "version": "3.0.0", "resolved": REGISTRY + "/kleur/-/kleur-3.0.0.tgz",
                "integrity": "sha512-" + base64.b64encode(bytes(64)).decode(),
            }}}
            manifest_path = project / "package.json"
            lock_path = project / "package-lock.json"
            manifest_path.write_bytes((json.dumps(manifest, indent=2) + "\n").encode())
            lock_bytes = json.dumps(lock).encode()
            lock_path.write_bytes(lock_bytes)
            npmrc = project / ".npmrc"
            settings = b"package-lock=false\nmin-release-age=7\nignore-scripts=true\nallow-git=none\n"
            npmrc.write_bytes(settings)
            request = {**self.request, "responseMode": "EDITS", "editsStrategy": "NATIVE_INTENT", "scope": [
                {"selector": {"owner": ".", "section": "devDependencies", "name": "kleur"},
                 "approvedLines": [3], "exactTarget": "3.0.1"},
            ]}
            with self.assertRaisesRegex(GoalContractError, "WORKSPACE_PATTERN_UNSUPPORTED"):
                observe_npm_workspace(project)
            with patch.dict(os.environ, {"DEPLOOM_WORKSPACE_NPM_REGISTRY": REGISTRY}), patch(
                    "deploom_mcp.workspace_edits.configured_state_root", return_value=root / "state"):
                result = asyncio.run(plan_workspace_edits(str(project), request))
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["authority"], "MANIFEST_INTENT_ONLY")
            artifact = Path(result["patch"]["path"])
            binding = (project, result["inventoryDigest"], artifact, result["patch"]["sha256"])
            self.assertEqual(result["action"]["argv"], ["deploom-workspace", "apply", *map(str, binding)])
            npmrc.write_bytes(settings + b"save-exact=true\n")
            with self.assertRaisesRegex(GoalContractError, "STALE_WORKSPACE_INVENTORY"):
                apply_workspace_patch(*binding)
            self.assertEqual(json.loads(manifest_path.read_bytes()), manifest)
            npmrc.write_bytes(settings)
            apply_workspace_patch(*binding)
            self.assertEqual(json.loads(manifest_path.read_bytes())["devDependencies"]["kleur"], "3.0.1")
            self.assertEqual(lock_path.read_bytes(), lock_bytes)
            self.assertEqual(npmrc.read_bytes(), settings)

    def test_apply_preserves_manifest_eol_under_git_autocrlf(self):
        from deploom_mcp.workspace_edits import _write_patch

        path = self.root / "packages/a/package.json"
        manifest = json.loads(path.read_bytes())
        old = manifest["dependencies"]["kleur"]
        original = (json.dumps(manifest, indent=2) + "\n").encode()
        manifest["dependencies"]["kleur"] = "3.0.1"
        expected = (json.dumps(manifest, indent=2) + "\n").encode()
        edits = [{"owner": "packages/a", "section": "dependencies", "name": "kleur",
                  "fromSpec": old, "toVersion": "3.0.1"}]
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.autocrlf", "GIT_CONFIG_VALUE_0": "true",
        }), patch("deploom_mcp.workspace_edits.configured_state_root", return_value=Path(directory)):
            for eol in (b"\n", b"\r\n"):
                with self.subTest(eol=eol):
                    path.write_bytes(original.replace(b"\n", eol))
                    inventory = observe_npm_workspace(self.root)["inventoryDigest"]
                    artifact = _write_patch(self.root, inventory, edits)
                    apply_workspace_patch(self.root, inventory, Path(artifact["path"]), artifact["sha256"])
                    self.assertEqual(path.read_bytes(), expected.replace(b"\n", eol))

    def test_eslint_card_flags_local_configuration_and_invocation_before_apply(self):
        manifest = self.fixture.manifests["package.json"]
        manifest["devDependencies"]["eslint"] = "8.57.1"
        self.fixture.lock["packages"][""]["devDependencies"]["eslint"] = "8.57.1"
        self.fixture.lock["packages"]["node_modules/eslint"] = {
            "version": "8.57.1", "resolved": f"{REGISTRY}/eslint/-/eslint-8.57.1.tgz",
            "integrity": "sha512-" + base64.b64encode(bytes(64)).decode(),
        }
        manifest["eslintConfig"] = {"rules": {"no-unused-vars": ["error", {"args": "none"}]},
                                    "overrides": [{"files": ["**/*.md"], "processor": "markdown/markdown"}]}
        (self.root / "test").mkdir()
        nested = b"env:\n  mocha: true\n"
        (self.root / "test/.eslintrc.yml").write_bytes(nested)
        manifest["scripts"] = {
            "lint": "cross-env ESLINT_USE_FLAT_CONFIG=false eslint --ext .ts .",
            "lint:ci": "npm exec -- eslint --ignore-path=.gitignore .",
            "message": "echo eslint --env=browser",
            "lint:flat": "ESLINT_USE_FLAT_CONFIG=true eslint .",
        }
        ignore = b"generated.js\n"
        (self.root / ".eslintignore").write_bytes(ignore)
        self.fixture.save()
        request = {**self.request, "responseMode": "EDITS", "editsStrategy": "NATIVE_INTENT",
                   "migrationCard": True, "scope": [{
                       "selector": {"name": "eslint", "owner": ".", "section": "devDependencies"},
                       "approvedLines": [9], "exactTarget": "9.0.0",
                   }]}
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"DEPLOOM_WORKSPACE_NPM_REGISTRY": REGISTRY},
        ), patch("deploom_mcp.workspace_edits.configured_state_root", return_value=Path(directory)):
            result = asyncio.run(plan_workspace_edits(str(self.root), request))
        self.assertTrue(result["ok"], result)
        card = result["migrationCard"]["transitions"][0]
        manifest_hash = sha256((self.root / "package.json").read_bytes()).hexdigest()
        self.assertEqual(card["observations"], [{
            "path": "package.json", "sha256": manifest_hash, "format": "eslintrc", "mentionsCiEnv": False,
        }])
        self.assertEqual(card["invocationObservations"], {
            "path": "package.json", "sha256": manifest_hash, "scripts": [
                {"script": "lint", "legacyOptions": ["--ext"], "forcesLegacyConfig": True},
                {"script": "lint:ci", "legacyOptions": ["--ignore-path"], "forcesLegacyConfig": False},
            ],
        })
        self.assertEqual(card["ignoreObservation"], {"path": ".eslintignore", "sha256": sha256(ignore).hexdigest()})
        self.assertIn("matching flat-config files globs", card["guidance"])
        self.assertEqual(card["ruleObservations"], [{"rule": "no-unused-vars", "paths": ["package.json"]}])
        self.assertIn("caughtErrors: none", card["guidance"])
        self.assertEqual(card["nestedTestConfigObservations"], [{
            "path": "test/.eslintrc.yml", "sha256": sha256(nested).hexdigest(), "mentionsMochaEnv": True,
        }])
        self.assertEqual(card["markdownProcessorPaths"], ["package.json"])
        self.assertIn("after broad JavaScript rules", card["guidance"])
        self.assertEqual({row["status"] for row in card["checks"]}, {"NOT_RUN"})

    def test_eslint_recipe_requires_host_digest_and_local_eligibility(self):
        from deploom_mcp.workspace_migration_card import describe_exact_migrations

        (self.root / ".eslintrc.yml").write_bytes(b"extends: standard\n")
        ignores = self.root / ".eslintignore"
        ignores.write_bytes(b"coverage\nnode_modules\n")
        target = {"declaration": [".", "devDependencies", "eslint"],
                  "baselineLock": ["node_modules/eslint", "8.57.1"], "selectedVersion": "9.0.0"}

        def card():
            return describe_exact_migrations(self.root, [target])["transitions"][0]

        recipe = {
            "schemaVersion": 1, "recipeVersion": 1, "verificationScope": "DONOR_ONLY",
            "transition": {"package": "eslint", "from": "8.57.1", "to": "9.0.0"},
            "donor": {"acceptedRun": {"sha256": "a" * 64}},
            "parts": [
                {"id": "legacy-ignore-directory-basenames",
                 "verifiedExamples": {"coverage": "**/coverage/", "node_modules": "**/node_modules/"}},
                {"id": "core-unused-vars-catch-default"},
            ],
        }
        payload = json.dumps(recipe).encode()
        digest = sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory) / "recipe.json"
            outside.write_bytes(payload)
            settings = {"DEPLOOM_MCP_MIGRATION_RECIPE_PATH": str(outside),
                        "DEPLOOM_MCP_MIGRATION_RECIPE_SHA256": digest}
            with patch.dict(os.environ, settings):
                observed = card()["recipe"]
                self.assertEqual(observed["applicability"], "RECIPIENT_PROBE_REQUIRED")
                self.assertEqual(observed["verificationScope"], "DONOR_ONLY")
                self.assertEqual(observed["parts"][0]["candidateIgnores"], ["**/coverage/", "**/node_modules/"])
                self.assertEqual(observed["parts"][1]["candidateOptions"], {"caughtErrors": "none"})
                ignores.write_bytes(b"coverage\n!coverage/keep\n")
                self.assertEqual([part["id"] for part in card()["recipe"]["parts"]],
                                 ["core-unused-vars-catch-default"])
                outside.write_bytes(payload + b" ")
                self.assertNotIn("recipe", card())
                inside = self.root / "recipe.json"
                inside.write_bytes(payload)
                with patch.dict(os.environ, {"DEPLOOM_MCP_MIGRATION_RECIPE_PATH": str(inside)}):
                    self.assertNotIn("recipe", card())

    def test_native_intent_cli_needs_no_event_loop(self):
        from deploom_mcp.workspace_edits import main

        target = self.request["scope"][0]
        request = {**self.request, "responseMode": "EDITS", "editsStrategy": "NATIVE_INTENT",
                   "scope": [{**target, "selector": {**target["selector"], "section": "dependencies"}}]}
        self.fixture.lock["packages"]["node_modules/kleur"].update(
            resolved=REGISTRY + "/kleur/-/kleur-3.0.0.tgz",
            integrity="sha512-" + base64.b64encode(bytes(64)).decode(),
        )
        self.fixture.save()
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            request_path = state / "request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            output = StringIO()
            with patch.object(sys, "argv", ["deploom-workspace", "plan", str(self.root), str(request_path)]), patch.dict(
                os.environ, {"DEPLOOM_WORKSPACE_NPM_REGISTRY": REGISTRY}), patch(
                "deploom_mcp.workspace_edits.configured_state_root", return_value=state), patch(
                "deploom_mcp.workspace_edits.asyncio.run", side_effect=AssertionError("Unexpected event loop")), redirect_stdout(output):
                self.assertEqual(main(), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["authority"], "MANIFEST_INTENT_ONLY")
            self.assertIn(b'"3.0.1"', Path(result["patch"]["path"]).read_bytes())

    def test_native_intent_groups_exact_pins_across_owners_in_one_patch(self):
        registry = {"DEPLOOM_WORKSPACE_NPM_REGISTRY": REGISTRY}
        packages = self.fixture.lock["packages"]
        for path in ("node_modules/kleur", "packages/b/node_modules/kleur"):
            version = packages[path]["version"]
            packages[path].update(
                resolved=f"{REGISTRY}/kleur/-/kleur-{version}.tgz",
                integrity="sha512-" + base64.b64encode(bytes(64)).decode(),
            )
        self.fixture.save()
        a, b = ({"selector": {"name": "kleur", "owner": f"packages/{owner}", "section": "dependencies"},
                  "approvedLines": [major], "exactTarget": f"{major}.0.1"}
                for owner, major in (("a", 3), ("b", 4)))
        request = {**self.request, "responseMode": "EDITS", "editsStrategy": "NATIVE_INTENT",
                   "scope": [b, a], "migrationCard": True}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, registry), patch(
                "deploom_mcp.workspace_edits.configured_state_root", return_value=Path(directory)):
            for scope, error in (
                ([a, a], "DUPLICATE_WORKSPACE_SELECTOR"),
                ([a, {key: value for key, value in b.items() if key != "exactTarget"}],
                 "NATIVE_INTENT_REQUIRES_EXACT_TARGETS"),
                ([{**a, "exactTarget": "3.0.0"}, {**b, "exactTarget": "4.0.0"}],
                 "NATIVE_INTENT_REQUIRES_UPGRADE"),
            ):
                with self.subTest(error=error):
                    blocked = asyncio.run(plan_workspace_edits(str(self.root), {**request, "scope": scope}))
                    self.assertEqual(blocked["error"]["code"], error)
                    self.assertEqual(list(Path(directory).glob("*.patch")), [])
            request["scope"] = [{**b, "exactTarget": "4.0.0"}, a]
            plan = asyncio.run(plan_workspace_edits(str(self.root), request))
            self.assertTrue(plan["ok"], plan)
            self.assertNotIn("target", plan)
            self.assertEqual([row["owner"] for row in plan["targets"]], ["packages/a", "packages/b"])
            self.assertEqual(plan["migrationCard"]["uncoveredCount"], 1)
            subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
            apply_workspace_patch(self.root, plan["inventoryDigest"], Path(plan["patch"]["path"]),
                                  plan["patch"]["sha256"])
            left = json.loads((self.root / "packages/a/package.json").read_bytes())
            right = json.loads((self.root / "packages/b/package.json").read_bytes())
            self.assertEqual(left["dependencies"]["kleur"], "3.0.1")
            self.assertEqual(right["dependencies"]["kleur"], "4.0.0")
            self.assertEqual(right["peerDependencies"]["kleur"], "^4.0.0")

    def test_native_intent_requires_explicit_authority_for_a_companion_addition(self):
        package = self.fixture.lock["packages"]["node_modules/kleur"]
        package.update(resolved=f"{REGISTRY}/kleur/-/kleur-3.0.0.tgz",
                       integrity="sha512-" + base64.b64encode(bytes(64)).decode())
        self.fixture.save()
        update = {"selector": {"name": "kleur", "owner": "packages/a", "section": "dependencies"},
                  "approvedLines": [3], "exactTarget": "3.0.1"}
        addition = {"selector": {"name": "fixture-companion", "owner": "packages/a", "section": "dependencies"},
                    "approvedLines": [5], "exactTarget": "5.0.0", "operation": "ADD"}
        request = {**self.request, "responseMode": "EDITS", "editsStrategy": "NATIVE_INTENT"}
        lock = self.root / "package-lock.json"
        original_lock = lock.read_bytes()
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"DEPLOOM_WORKSPACE_NPM_REGISTRY": REGISTRY},
        ), patch("deploom_mcp.workspace_edits.configured_state_root", return_value=Path(directory)):
            for scope, error in (
                ([update, {key: value for key, value in addition.items() if key != "operation"}],
                 "WORKSPACE_SCOPE_NOT_FOUND"),
                ([{**update, "operation": "ADD"}, addition], "NATIVE_INTENT_ADD_ALREADY_DECLARED"),
                ([addition], "NATIVE_INTENT_REQUIRES_UPGRADE"),
            ):
                with self.subTest(error=error):
                    blocked = asyncio.run(plan_workspace_edits(str(self.root), {**request, "scope": scope}))
                    self.assertEqual(blocked["error"]["code"], error)
            plan = asyncio.run(plan_workspace_edits(str(self.root), {**request, "scope": [update, addition]}))
            self.assertTrue(plan["ok"], plan)
            self.assertIsNone(plan["targets"][0]["fromSpec"])
            manifest_path = self.root / "packages/a/package.json"
            original_manifest = manifest_path.read_bytes()
            drifted_manifest = original_manifest + b"\n"
            manifest_path.write_bytes(drifted_manifest)
            with self.assertRaisesRegex(GoalContractError, "STALE_WORKSPACE_INVENTORY"):
                apply_workspace_patch(self.root, plan["inventoryDigest"], Path(plan["patch"]["path"]),
                                      plan["patch"]["sha256"])
            self.assertEqual(manifest_path.read_bytes(), drifted_manifest)
            self.assertEqual(lock.read_bytes(), original_lock)
            manifest_path.write_bytes(original_manifest)
            apply_workspace_patch(self.root, plan["inventoryDigest"], Path(plan["patch"]["path"]),
                                  plan["patch"]["sha256"])
        manifest = json.loads(manifest_path.read_bytes())
        self.assertEqual(manifest["dependencies"], {"kleur": "3.0.1", "fixture-companion": "5.0.0"})
        self.assertEqual(lock.read_bytes(), original_lock)

    def test_compact_edits_reuses_manifest_patch_and_keeps_blocker_details(self):
        request = {**self.request, "responseMode": "EDITS"}
        with tempfile.TemporaryDirectory() as directory:
            with patch("deploom_mcp.workspace_edits.configured_state_root", return_value=Path(directory)):
                full = self.run_plan(request)
                compact = self.run_plan({**request, "compactEdits": True})
            self.assertTrue(compact["compactEdits"])
            for key in ("inventoryDigest", "action", "verification", "executionReady", "changeCount"):
                self.assertEqual(compact[key], full[key])
            self.assertEqual(compact["patch"]["sha256"], full["patch"]["sha256"])
            self.assertEqual(Path(compact["patch"]["path"]).read_bytes(), Path(full["patch"]["path"]).read_bytes())
            self.assertNotIn("details", compact)

        blocked = json.loads(json.dumps(request))
        blocked["scope"][0]["exactTarget"] = "3.0.9"
        full = self.run_plan(blocked)
        compact = self.run_plan({**blocked, "compactEdits": True})
        self.assertEqual(compact["constraintModel"], full["constraintModel"])
        self.assertEqual(compact["action"], full["action"])
        self.assertNotIn("compactEdits", compact)
        self.assertIsNone(compact["patch"])
        self.assertEqual(self.run_plan({**request, "compactEdits": "true"})["error"]["code"],
                         "INVALID_WORKSPACE_COMPACT_EDITS_OPTION")

    def test_edits_mode_does_not_emit_patch_for_unknown(self):
        request = json.loads(json.dumps(self.request))
        request["responseMode"] = "EDITS"
        request["scope"][0]["exactTarget"] = "3.0.9"
        result = self.run_plan(request)
        self.assertEqual(result["constraintModel"]["status"], "UNKNOWN")
        self.assertIsNone(result["patch"])

    def test_held_workspace_owner_peer_limits_root_provider(self):
        self.fixture.manifests['packages/b/package.json']['peerDependencies']['typescript'] = '>=5 <5.0.5'
        self.fixture.lock['packages']['packages/b']['peerDependencies']['typescript'] = '>=5 <5.0.5'
        self.fixture.save()

        def metadata(name):
            versions = ['5.0.0', '5.0.4', '5.0.5'] if name == 'typescript' else ['3.0.0', '3.0.1', '4.0.0']
            rows = {v: {'name': name, 'version': v, 'dist': {
                'tarball': f'{REGISTRY}/{name}/-/{name}-{v}.tgz'}} for v in versions}
            if name == 'kleur':
                rows['3.0.1']['peerDependencies'] = {'typescript': '>=5.0.4'}
            return {'versions': rows}, True

        result = self.run_plan(metadata_source=metadata)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['constraintModel']['status'], 'SAT_IN_MODELED_DOMAIN')
        versions = {row['declaration'][2]: row['selectedVersion'] for row in result['jointAssignment']['targets']}
        self.assertEqual(versions['typescript'], '5.0.4')
        self.assertEqual(result['coverage']['ownerPeerConstraintCount'], 1)
        self.assertEqual(result['coverage']['constraintDeclarationCount'], 3)

        def stale_manifest():
            path = self.root / 'packages/b/package.json'
            manifest = json.loads(path.read_text(encoding='utf-8'))
            manifest['peerDependencies']['typescript'] = '<5'
            path.write_text(json.dumps(manifest), encoding='utf-8')

        stale = self.run_plan(metadata_source=metadata, before_worker=stale_manifest)
        self.assertFalse(stale['ok'])
        self.assertEqual(stale['error']['code'], 'WORKSPACE_MANIFEST_LOCK_MISMATCH')

    def test_resolves_scope_selects_bounded_candidates_and_holds_other_owner(self):
        result = self.run_plan()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['constraintModel']['status'], 'SAT_IN_MODELED_DOMAIN')
        self.assertEqual(result['constraintModel']['domain']['selection'], 'SERVER_BOUNDED')
        self.assertEqual(result['intent']['candidateSelection']['proofScope'], 'SELECTED_DOMAIN_ONLY')
        self.assertTrue(result['intent']['candidateSelection']['truncated'])
        targets = result['intent']['targets']
        self.assertEqual(targets[0]['versions'], ['3.0.1'])
        self.assertEqual(len(targets[1]['versions']), 8)
        self.assertIn('5.0.0', targets[1]['versions'])
        self.assertEqual(result['coverage']['heldDeclarationCount'], 1)
        self.assertFalse(result['executionReady'])
        self.assertEqual(result['installation']['status'], 'NOT_RUN')

    def test_ambiguous_name_returns_selectors_before_solver(self):
        request = {**self.request, 'scope': [{'selector': {'name': 'kleur'},
                                            'approvedLines': [3], 'exactTarget': '3.0.1'}]}
        result = self.run_plan(request)
        self.assertEqual(result['error']['code'], 'AMBIGUOUS_SCOPE')
        self.assertEqual(result['error']['matchCount'], 2)
        self.assertEqual([row['owner'] for row in result['error']['validSelectors']], ['packages/a', 'packages/b'])

    def test_drift_between_discovery_and_plan_is_rejected(self):
        result = self.run_plan(before_worker=lambda: (self.root / '.npmrc').write_text('save-exact=true'))
        self.assertEqual(result['error']['code'], 'STALE_WORKSPACE_INVENTORY')

    def test_exact_unknown_target_is_not_replaced_by_baseline(self):
        request = json.loads(json.dumps(self.request))
        request['scope'][0]['exactTarget'] = '3.0.9'
        result = self.run_plan(request)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['constraintModel']['status'], 'UNKNOWN')
        self.assertEqual(result['intent']['targets'][0]['versions'], ['3.0.9'])
        self.assertIsNone(result['candidateId'])

    def test_truncated_unsat_suggests_expansion_and_explicit_v2_stays_explicit(self):
        self.fixture.lock['packages']['node_modules/kleur']['peerDependencies'] = {'typescript': '^5'}
        self.fixture.save()
        def metadata(name):
            versions = ['5.0.0', '5.0.1', '5.0.2'] if name == 'typescript' else ['3.0.0', '3.0.1', '4.0.0']
            records = {v: {'name': name, 'version': v, 'dist': {
                'tarball': f'{REGISTRY}/{name}/-/{name}-{v}.tgz'}} for v in versions}
            if name == 'kleur':
                records['3.0.0']['peerDependencies'] = {'typescript': '^5'}
                records['3.0.1']['peerDependencies'] = {'typescript': '5.0.1'}
            return {'versions': records}, True
        small = self.run_plan({**self.request, 'candidateLimit': 2}, metadata_source=metadata)
        self.assertTrue(small['ok'], small)
        self.assertEqual(small['constraintModel']['status'], 'UNSAT_IN_MODELED_DOMAIN')
        self.assertEqual(small['action']['kind'], 'EXPAND_SELECTED_DOMAIN')
        large = self.run_plan({**self.request, 'candidateLimit': 3}, metadata_source=metadata)
        self.assertEqual(large['constraintModel']['status'], 'SAT_IN_MODELED_DOMAIN')
        self.assertFalse(large['intent']['candidateSelection']['truncated'])
        explicit = {'schemaVersion': 2, 'goalKind': 'TARGETED_UPGRADE', 'registry': REGISTRY,
                    'inventoryDigest': observe_npm_workspace(self.root)['inventoryDigest'],
                    'environment': {}, 'topology': None, 'targets': [
            {'selector': {'owner': 'packages/a', 'section': 'dependencies', 'name': 'kleur'},
             'approvedLines': [3], 'versions': ['3.0.1'], 'exactTarget': '3.0.1'},
            {'selector': {'owner': '.', 'section': 'devDependencies', 'name': 'typescript'},
             'approvedLines': [5], 'versions': ['5.0.2', '5.0.0'], 'exactTarget': None}]}
        legacy = self.run_plan(explicit, metadata_source=metadata)
        self.assertEqual(legacy['constraintModel']['status'], 'UNSAT_IN_MODELED_DOMAIN')
        self.assertEqual(legacy['intent']['targets'], explicit['targets'])
        self.assertNotIn('candidateSelection', legacy['intent'])

    def test_already_satisfied_target_is_not_a_completed_upgrade(self):
        request = json.loads(json.dumps(self.request))
        request['scope'][0]['exactTarget'] = '3.0.0'
        result = self.run_plan(request)
        self.assertEqual(result['error']['code'], 'WORKSPACE_TARGET_ALREADY_SATISFIED')

    def test_noop_ambiguity_and_authority_expansion_are_not_inferred(self):
        cases = [({**self.request, 'scope': [{'selector': {'name': 'kleur'}, 'approvedLines': [3]}]},
                  'WORKSPACE_TARGETED_GOAL_REQUIRES_EXACT_TARGET'),
                 ({**self.request, 'candidateLimit': True}, 'INVALID_WORKSPACE_CANDIDATE_LIMIT'),
                 ({**self.request, 'topology': {}}, 'INVALID_WORKSPACE_GOAL_REQUEST'),
                 ({**self.request, 'packuments': {}}, 'INVALID_WORKSPACE_GOAL_REQUEST')]
        for request, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(GoalContractError, code):
                validate_goal_request(request)
