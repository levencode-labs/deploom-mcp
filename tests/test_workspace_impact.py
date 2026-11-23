import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploom_mcp.workspace_impact import build_project_impact


def target(package, before, after, owner="."):
    return {"declaration": [owner, "dependencies", package], "baselineLock": ["unused", before],
            "selectedVersion": after}


class WorkspaceImpactTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name) / "project"
        self.project.mkdir()
        state = patch("deploom_mcp.workspace_impact.configured_state_root", return_value=self.project.parent / "state")
        state.start()
        self.addCleanup(state.stop)

    def write(self, name, text):
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_config_filename_without_parser_is_only_partial(self):
        self.write("tsconfig.json", '{"compilerOptions":{"baseUrl":"src"}}')
        self.write("packages/held/tsconfig.json", "{}")
        with patch.dict(os.environ, {"DEPLOOM_MCP_TYPESCRIPT_PATH": "", "DEPLOOM_MCP_TYPESCRIPT_SHA256": ""}):
            result = build_project_impact(self.project, [target("typescript", "5.9.3", "6.0.3")],
                                          [".", "packages/held"], "a" * 64)
        row = result["transitions"][0]
        self.assertEqual(row["affectedFiles"], ["tsconfig.json"])
        self.assertEqual(row["applicableChanges"], [])
        self.assertEqual(result["coverage"]["status"], "UNKNOWN")

    def test_corporate_and_offline_policies_keep_reference_lookup_local(self):
        row = target("typescript", "5.9.3", "6.0.3")
        row["publicNpmIntegrity"] = "sha512-fixture"
        settings = ("DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE", "DEPLOOM_MCP_RECORDS_MIRROR_ONLY",
                    "DEPLOOM_MCP_MIGRATION_OFFLINE", "DEPLOOM_MCP_LOCAL_CACHE_ONLY")
        for setting in settings:
            with self.subTest(setting=setting), patch.dict(os.environ, {setting: "1"}), patch(
                "deploom_mcp.workspace_impact.collect_workspace_releases",
                return_value={"rows": [{"status": "UNKNOWN"}], "metrics": {"httpTransactions": 0}},
            ) as collect:
                build_project_impact(self.project, [row], ["."], "a" * 64,
                                     registry="https://registry.npmjs.org", evidence_mode="ON_DEMAND")
                self.assertTrue(collect.call_args.kwargs["offline"])
        with patch("deploom_mcp.workspace_impact.collect_workspace_releases") as collect:
            build_project_impact(self.project, [row], ["."], "a" * 64,
                                 registry="https://mirror.example.invalid/npm", evidence_mode="ON_DEMAND")
            collect.assert_not_called()

    @unittest.skipUnless(os.environ.get("DEPLOOM_MCP_TYPESCRIPT_PATH"), "requires the pinned host parser")
    def test_parser_matches_only_the_selected_owner_and_exact_transition(self):
        self.write("packages/migrate/src/app.ts", "export {render as mount} from 'react-dom';")
        self.write("packages/migrate/src/client.ts", "const load = import(`react-dom/client`);")
        self.write("packages/migrate/src/ns.ts",
                   "import * as DOM from 'react-dom'; DOM['render'](app); "
                   "function shadow(DOM) { DOM.hydrate(app); }")
        self.write("packages/migrate/tsconfig.json", '{ // JSONC is allowed\n "compilerOptions":{"baseUrl":"src"},}')
        self.write("packages/held/app.ts", "import {hydrate} from 'react-dom';")
        self.write("packages/held/component.vue", "<template/>")
        self.write("entry.ts", "const {unmountComponentAtNode: unmount} = require('react-dom');")
        result = build_project_impact(
            self.project, [target("react-dom", "18.3.1", "19.0.0", "packages/migrate"),
                           target("react-dom", "18.2.0", "18.3.1"),
                           target("typescript", "5.9.3", "6.0.3", "packages/migrate")],
            [".", "packages/migrate", "packages/held"], "b" * 64,
        )
        react, patch_release, ts = result["transitions"]
        self.assertEqual(react["affectedFiles"],
                         ["packages/migrate/src/app.ts", "packages/migrate/src/client.ts", "packages/migrate/src/ns.ts"])
        self.assertEqual({row["form"] for row in react["matchedPatterns"]},
                         {"MODULE_REEXPORT", "DYNAMIC_IMPORT_LITERAL", "MODULE_IMPORT", "IMPORTED_MEMBER_ACCESS"})
        member_names = [row["knownImportedSymbols"] for row in react["matchedPatterns"]
                        if row["form"] == "IMPORTED_MEMBER_ACCESS"]
        self.assertEqual(member_names, [["render"]])
        self.assertEqual(react["potentialConsumersOutsideDeclarationOwners"], [".", "packages/held"])
        self.assertEqual([row["id"] for row in react["applicableChanges"]], ["react-dom-legacy-apis"])
        self.assertEqual(react["availableCodemods"][0]["id"], "react/19/replace-reactdom-render")
        self.assertEqual(patch_release["affectedFiles"], ["entry.ts"])
        self.assertEqual(patch_release["applicableChanges"], [])
        self.assertEqual([row["id"] for row in ts["applicableChanges"]], ["typescript-base-url"])
        self.assertEqual(result["coverage"]["status"], "UNKNOWN")
        self.assertEqual(result["coverage"]["files"]["unsupported"], 1)
