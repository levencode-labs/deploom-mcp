"""Advisory project observations for the exact transitions selected by EDITS."""
from __future__ import annotations

import re
import time
from collections import defaultdict
from pathlib import Path, PurePosixPath

from semantic_version import Version

from deploom_mcp.goal_execution_registry import configured_state_root
from deploom_mcp.migration_usage import inspect_usage
from deploom_mcp.workspace_inventory import _read_bytes

_SCRIPT = r"\.[cm]?[jt]s"
_CONFIGS = {
    "typescript": (re.compile(r"tsconfig(?:\.[a-zA-Z0-9_-]+)*\.json"), ("compilerOptions.baseUrl",)),
    "vite": (re.compile(r"vite\.config" + _SCRIPT), ()),
    "vitest": (re.compile(r"(?:vitest|vite)\.config" + _SCRIPT), ("test.poolOptions",)),
    "eslint": (re.compile(r"eslint\.config" + _SCRIPT), ()),
    "jest": (re.compile(r"jest\.config(?:" + _SCRIPT + r"|\.json)"), ()),
}

# One rule is a version threshold and a positive syntax selector. Findings
# outside these selectors still identify files for manual review.
_RULES = {
    "typescript": (
        ("6.0.0", "typescript-base-url", "key", ("compilerOptions.baseUrl",),
         "Review the deprecated baseUrl option. Preserve module resolution with explicit relative paths or paths mappings.",
         "Run the existing typecheck in each affected workspace and verify imports using the old baseUrl.",
         "https://www.typescriptlang.org/docs/handbook/release-notes/typescript-6-0.html#deprecated---baseurl"),
    ),
    "react-dom": (
        ("19.0.0", "react-dom-legacy-apis", "symbol", ("render", "hydrate", "unmountComponentAtNode", "findDOMNode"),
         "Check the observed react-dom API against the React 19 replacements before editing its callers.",
         "Verify mount, hydration or unmount behavior for the changed entry point.",
         "https://react.dev/blog/2024/04/25/react-19-upgrade-guide#removed-react-dom-apis"),
    ),
    "vite": (
        ("7.0.0", "vite-split-vendor-chunks", "symbol", ("splitVendorChunkPlugin",),
         "Remove the legacy plugin or preserve its intended chunking with explicit manualChunks.",
         "Compare production chunk boundaries and load a representative page.",
         "https://v7.vite.dev/guide/migration#removed-deprecated-features"),
        ("7.0.0", "vite-sass-legacy-api", "value", ("legacy",),
         "Update the observed Sass API setting and review the stylesheet integration for Vite 7.",
         "Build and compare a representative Sass stylesheet.",
         "https://v7.vite.dev/guide/migration"),
    ),
    "vitest": (
        ("4.0.0", "vitest-pool-options", "key", ("test.poolOptions",),
         "Review the observed poolOptions configuration against the Vitest 4 pool options.",
         "Run the existing tests that depend on the pool or worker isolation settings.",
         "https://v4.vitest.dev/guide/migration#pool-rework"),
    ),
    "strip-json-comments": (
        ("4.0.0", "strip-json-comments-module-format", "form", ("REQUIRE_LITERAL", "IMPORT_EQUALS_REQUIRE"),
         "Check how the require site handles the ESM default export with the project's Node version.",
         "Run the existing caller against JSON comments and trailing comma fixtures.",
         "https://github.com/sindresorhus/strip-json-comments/releases/tag/v4.0.0"),
    ),
}

def _config_files(project: Path, pairs, deadline):
    """Report literal config filenames even when the pinned parser is unavailable."""
    rows = []
    for package, owner in pairs:
        if package not in _CONFIGS or time.monotonic() >= deadline:
            continue
        filenames = {
            "typescript": ("tsconfig.json", "tsconfig.build.json"),
            "vite": ("vite.config.ts", "vite.config.js", "vite.config.mts", "vite.config.mjs"),
            "vitest": ("vitest.config.ts", "vitest.config.js", "vite.config.ts", "vite.config.js"),
            "eslint": ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", ".eslintrc", ".eslintrc.json"),
            "jest": ("jest.config.js", "jest.config.ts", "jest.config.cjs", "jest.config.mjs", "jest.config.json"),
        }[package]
        for name in filenames:
            path = project / owner / name
            try:
                _read_bytes(path, 131072, root=project)
            except (OSError, ValueError):
                continue
            rows.append({"package": package, "path": path.relative_to(project).as_posix(), "form": "CONFIG_FILE"})
    return rows

def _signals(rows, before, after, package):
    changes = []
    for release, identifier, field, selectors, action, check, url in _RULES.get(package, ()):
        if not before < Version(release) <= after:
            continue
        matches = []
        for index, finding in enumerate(rows):
            if field == "symbol":
                if finding.get("typeOnly") or finding.get("specifier") != package:
                    continue
                values = finding.get("knownImportedSymbols", ())
            else:
                values = (finding.get(field),)
            if any(value in selectors for value in values):
                matches.append(index)
        if matches:
            changes.append({
                "id": identifier, "status": "STATIC_MATCH_REVIEW_REQUIRED",
                "matchedPatterns": matches, "source": url, "sourceAuthority": "OFFICIAL_REFERENCE",
                "suggestedAction": action,
                "suggestedCheck": {"status": "NOT_RUN", "instruction": check},
            })
    return changes

def build_project_impact(project: Path, targets: list[dict], owners: list[str], inventory_digest: str) -> dict:
    # Bound response and parsing work independently of the size of the solver assignment.
    transitions = defaultdict(set)
    unresolved = 0
    for row in targets:
        owner, _, package = row["declaration"]
        locked = row["baselineLock"]
        before, after = locked[1] if locked else None, row["selectedVersion"]
        if before == after:
            continue
        try:
            old, new = Version(before), Version(after)
        except (TypeError, ValueError):
            unresolved += 1
            continue
        transitions[package, old, new].add(owner)
    selected = list(transitions.items())[:8]
    result = {"authority": "RECOMMENDATION_ONLY", "coverage": None, "transitions": [],
              "inventoryDigest": inventory_digest, "omittedTransitions": len(transitions) - len(selected),
              "unresolvedTransitions": unresolved}
    if not selected:
        return result
    packages = sorted({package for (package, _, _), _ in selected})
    state = configured_state_root()
    deadline = time.monotonic() + 5
    coverage = inspect_usage(project, packages, state, deadline=deadline, source_identity=inventory_digest,
                             config_keys=[(pattern, package, keys) for package, (pattern, keys) in _CONFIGS.items()
                                          if package in packages])
    findings = coverage["observations"]
    if coverage["localCoverage"] is None and coverage["reason"] == "LOCAL_INSPECTION_UNAVAILABLE":
        findings = _config_files(project, sorted({(package, owner) for (package, _, _), group in selected for owner in group}), deadline)
    result["coverage"] = {"status": coverage["status"], "reason": coverage["reason"],
                          "files": coverage["localCoverage"], "fileManifestDigest": coverage.get("fileManifestDigest")}
    known_owners = sorted(
        ((PurePosixPath(owner).parts, owner) for owner in set(owners) - {"."}),
        key=lambda item: (-len(item[0]), item[1]),
    )
    observations_by_package = defaultdict(list)
    for finding in findings:
        parts = PurePosixPath(finding["path"]).parts
        owner = next((owner for prefix, owner in known_owners if parts[:len(prefix)] == prefix), ".")
        observations_by_package[finding["package"]].append({
            **{k: v for k, v in finding.items() if k not in {"package", "semanticApplicability"}},
            "workspace": owner,
        })

    for (package, before, after), declarations in selected:
        current = observations_by_package[package]
        matching_owner = [row for row in current if row["workspace"] in declarations]
        rows = matching_owner[:12]
        outside_owners = sorted({row["workspace"] for row in current if row["workspace"] not in declarations})
        changes = _signals(rows, before, after, package)
        render_symbols = {"render", "hydrate", "unmountComponentAtNode"}
        codemods = []
        if any(change["id"] == "react-dom-legacy-apis" and any(
            render_symbols.intersection(rows[index]["knownImportedSymbols"])
            for index in change["matchedPatterns"]
        ) for change in changes):
            codemods.append({
                "id": "react/19/replace-reactdom-render", "status": "REVIEW_REQUIRED",
                "source": "https://react.dev/blog/2024/04/25/react-19-upgrade-guide#removed-reactdomrender",
                "verification": "Review the transform against the observed call sites, inspect the diff, and verify mount, hydration and unmount as applicable.",
            })
        result["transitions"].append({
            "package": package, "fromVersion": str(before), "toVersion": str(after),
            "dependencyOwners": sorted(declarations),
            "affectedWorkspaces": sorted({row["workspace"] for row in rows}),
            "affectedFiles": sorted({row["path"] for row in rows}),
            "matchedPatterns": rows, "omittedPatterns": len(matching_owner) - len(rows), "applicableChanges": changes,
            "potentialConsumersOutsideDeclarationOwners": outside_owners,
            "availableCodemods": codemods,
            "confidence": "STATIC_ONLY" if rows else "UNKNOWN",
            "unknowns": ["EFFECTIVE_CONFIG_AND_MODULE_RESOLUTION", "CROSS_WORKSPACE_VERSION_ATTRIBUTION"],
        })
    return result
