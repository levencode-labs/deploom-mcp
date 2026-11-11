"""Bounded migration guidance for exact transitions selected by EDITS."""
from itertools import islice
from pathlib import Path
import hashlib
import os
import re

from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.workspace_input_io import bound_directory
from deploom_mcp.workspace_inventory import _read_bytes

ESLINT_TRANSITION = ("eslint", "8.57.1", "9.0.0")
ESLINT_V9_GUIDE = "https://github.com/eslint/eslint/blob/v9.0.0/docs/src/use/migrate-to-9.0.0.md"
_CONFIG_GUIDE = "https://eslint.org/docs/v9.x/use/configure/migration-guide#migrate-your-config-file"
_FLAT_CONFIGS = ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs")
_LEGACY_CONFIGS = (".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml")
_CI_REFERENCE = re.compile(r"""\bprocess\s*\.\s*env\s*(?:\.\s*CI\b|\[\s*['"]CI['"]\s*\])""")


def _eslint_card(project: Path, owner: str, section: str) -> dict:
    directory = project / owner
    observations = []
    incomplete = False
    for filename in (*_FLAT_CONFIGS, *_LEGACY_CONFIGS):
        path = directory / filename
        try:
            raw = _read_bytes(path, 64 * 1024, root=project)
            source = raw.decode("utf-8-sig")
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError, GoalContractError):
            incomplete = True
            continue
        observations.append({
            "path": path.relative_to(project).as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "format": "flat" if filename in _FLAT_CONFIGS else "eslintrc",
            "mentionsCiEnv": bool(_CI_REFERENCE.search(source)),
        })

    modes = {row["format"] for row in observations}
    mentions_ci = any(row["mentionsCiEnv"] for row in observations)
    if incomplete or modes not in ({"flat"}, {"eslintrc"}) or len(observations) != 1:
        guidance = "Determine the effective config and invocation for this workspace before changing files."
    elif modes == {"flat"}:
        guidance = "A flat config exists. Verify the effective rules under ESLint 9 before deciding whether it needs edits."
    else:
        guidance = "Review the existing configuration and move its effective behavior to flat config."
        if mentions_ci:
            guidance += (" Preserve the CI-dependent expression in the new config. "
                         "The configuration migrator can flatten a JavaScript config to one evaluated branch.")

    checks = ["Use the project's existing lint command and compare diagnostics with the expected baseline behavior."]
    if mentions_ci:
        checks.append("Run the same lint probe with CI unset and with CI=true. Compare rule severities in both modes.")
    return {
        "package": ESLINT_TRANSITION[0], "from": ESLINT_TRANSITION[1], "to": ESLINT_TRANSITION[2],
        "owner": owner, "section": section, "status": "REVIEW_REQUIRED",
        "observationScope": "OWNER_CONFIG_FILENAMES_AND_CI_SYNTAX",
        "localObservation": "PARTIAL" if incomplete else "OBSERVED",
        "observations": observations, "guidance": guidance,
        "sources": [ESLINT_V9_GUIDE, _CONFIG_GUIDE],
        "checks": [{"status": "NOT_RUN", "instruction": instruction} for instruction in checks],
    }


_STRIP_TRANSITION = ("strip-json-comments", "3.1.1", "5.0.0")
_REQUIRE_STRIP = re.compile(r"""\brequire\s*\(\s*['"]strip-json-comments['"]\s*\)""")


def _strip_card(project: Path, owner: str, section: str) -> dict:
    observations = []
    incomplete = False
    for directory in (project / owner, project / owner / "src"):
        try:
            with bound_directory(project, directory) as binding:
                target = directory if os.name == "nt" else binding.fd
                with os.scandir(target) as entries:
                    names = list(islice(entries, 65))
        except FileNotFoundError:
            continue
        except (OSError, GoalContractError):
            incomplete = True
            continue
        incomplete |= len(names) > 64
        for entry in sorted(names[:64], key=lambda item: item.name):
            path = directory / entry.name
            if path.suffix not in {".js", ".cjs", ".mjs"}:
                continue
            try:
                raw = _read_bytes(path, 64 * 1024, root=project)
                source = raw.decode("utf-8-sig")
            except (OSError, UnicodeError, GoalContractError):
                incomplete = True
                continue
            for match in islice(_REQUIRE_STRIP.finditer(source), 4):
                observations.append({
                    "path": path.relative_to(project).as_posix(),
                    "line": source.count("\n", 0, match.start()) + 1,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "kind": "REQUIRE_SYNTAX",
                })
            if len(observations) >= 8:
                incomplete = True
                break
        if len(observations) >= 8:
            break
    return {
        "package": _STRIP_TRANSITION[0], "from": _STRIP_TRANSITION[1], "to": _STRIP_TRANSITION[2],
        "owner": owner, "section": section, "status": "REVIEW_REQUIRED",
        "observationScope": "OWNER_AND_SRC_DIRECT_JS_FILES_REQUIRE_SYNTAX",
        "localObservation": "PARTIAL" if incomplete else "OBSERVED",
        "observations": observations[:8],
        "guidance": ("Version 5 uses an ESM default export. Follow each observed binding to its callers. "
                     "On Node versions that support require(esm), a direct require can yield a namespace "
                     "whose default is callable. Check whether existing interop already handles it. "
                     "Use an import or normalize the binding where needed for the project's supported runtime."),
        "sources": [
            "https://github.com/sindresorhus/strip-json-comments/blob/v5.0.0/index.js",
            "https://nodejs.org/download/release/v24.0.0/docs/api/modules.html#loading-ecmascript-modules-using-require",
        ],
        "checks": [{"status": "NOT_RUN", "instruction": "Run the existing CLI and compare its JSON output and exit status."}],
    }


def build_migration_card(project: Path, targets: list[dict], model_status: str) -> dict:
    result = {"schemaVersion": 1, "authority": "RECOMMENDATION_ONLY",
              "status": "NO_SELECTED_TRANSITION", "transitions": [], "uncoveredCount": 0}
    if model_status != "SAT_IN_MODELED_DOMAIN":
        return result
    for row in targets:
        owner, section, package = row["declaration"]
        locked = row["baselineLock"]
        before = locked[1] if locked else None
        after = row["selectedVersion"]
        if before == after:
            continue
        provider = {ESLINT_TRANSITION: _eslint_card, _STRIP_TRANSITION: _strip_card}.get((package, before, after))
        if provider is None or len(result["transitions"]) == 4:
            result["uncoveredCount"] += 1
            continue
        result["transitions"].append(provider(project, owner, section))
    if result["transitions"]:
        result["status"] = "PARTIAL" if result["uncoveredCount"] else "AVAILABLE"
    elif result["uncoveredCount"]:
        result["status"] = "UNSUPPORTED"
    return result
