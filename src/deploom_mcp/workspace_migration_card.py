"""Bounded migration guidance for exact transitions selected by EDITS."""
from pathlib import Path
import hashlib
import re

from deploom_mcp.goal_contract import GoalContractError
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
        if (package, before, after) != ESLINT_TRANSITION or len(result["transitions"]) == 4:
            result["uncoveredCount"] += 1
            continue
        result["transitions"].append(_eslint_card(project, owner, section))
    if result["transitions"]:
        result["status"] = "PARTIAL" if result["uncoveredCount"] else "AVAILABLE"
    elif result["uncoveredCount"]:
        result["status"] = "UNSUPPORTED"
    return result
