"""Bounded migration guidance for exact transitions selected by EDITS."""
from itertools import islice
from pathlib import Path
import hashlib
import json
import os
import re

from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.workspace_input_io import bound_directory
from deploom_mcp.workspace_inventory import _read, _read_bytes

ESLINT_TRANSITION = ("eslint", "8.57.1", "9.0.0")
ESLINT_V9_GUIDE = "https://github.com/eslint/eslint/blob/v9.0.0/docs/src/use/migrate-to-9.0.0.md"
_CONFIG_GUIDE = "https://eslint.org/docs/v9.x/use/configure/migration-guide#migrate-your-config-file"
_FLAT_CONFIGS = ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs")
_LEGACY_CONFIGS = (".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml")
_CI_REFERENCE = re.compile(r"""\bprocess\s*\.\s*env\s*(?:\.\s*CI\b|\[\s*['"]CI['"]\s*\])""")


_CLI_GUIDE = "https://github.com/eslint/eslint/blob/v9.0.0/docs/src/use/configure/migration-guide.md#cli-flag-changes"
_DIRECT_ESLINT = re.compile(
    r"(?:^|[;&|])\s*(?:cross-env\s+)?(?P<env>(?:[A-Za-z_]\w*=\S+\s+)*)"
    r"(?:(?:npx(?:\s+--no-install)?|npm\s+exec\s+--)\s+)?"
    r"(?:\./node_modules/\.bin/)?eslint(?=\s|$)(?P<args>[^;&|\r\n]*)"
)
_LEGACY_OPTIONS = re.compile(
    r"(?<!\S)--(?:env|ext|ignore-path|no-eslintrc|resolve-plugins-relative-to|rulesdir)(?=$|[=\s])"
)
_LEGACY_OVERRIDE = re.compile(r"(?:^|\s)ESLINT_USE_FLAT_CONFIG=(?:false|'false'|\"false\")(?=\s|$)")


_NO_UNUSED_VARS_KEY = re.compile(r"""(?<![\w@/.-])no-unused-vars['"]?\s*:""")


_MOCHA_ENV = re.compile(r"""(?<![\w./-])mocha['"]?\s*:\s*true\b""")
_MARKDOWN_PROCESSOR = re.compile(r"""(?<![\w./-])markdown/markdown(?=['"\s,}]|$)""")


def _eslint_script_observations(manifest: dict) -> list[dict]:
    scripts = manifest.get("scripts")
    if not isinstance(scripts, dict):
        return []
    observations = []
    for name, command in sorted(scripts.items()):
        if not isinstance(command, str):
            continue
        options = set()
        forces_legacy = False
        for match in _DIRECT_ESLINT.finditer(command):
            options.update(_LEGACY_OPTIONS.findall(match["args"]))
            forces_legacy |= bool(_LEGACY_OVERRIDE.search(match["env"]))
        if options or forces_legacy:
            observations.append({"script": name, "legacyOptions": sorted(options), "forcesLegacyConfig": forces_legacy})
    return observations


def _eslint_recipe(project: Path, ignore_text: str | None) -> dict | None:
    location = os.environ.get("DEPLOOM_MCP_MIGRATION_RECIPE_PATH")
    expected = os.environ.get("DEPLOOM_MCP_MIGRATION_RECIPE_SHA256", "")
    if not location or not re.fullmatch(r"[a-f0-9]{64}", expected):
        return None
    path = Path(location)
    try:
        if not path.is_absolute() or path.resolve().is_relative_to(project.resolve()):
            return None
        raw = _read_bytes(path, 32 * 1024)
        if hashlib.sha256(raw).hexdigest() != expected:
            return None
        recipe = json.loads(raw)
        transition = dict(zip(("package", "from", "to"), ESLINT_TRANSITION))
        if (recipe["schemaVersion"] != 1 or recipe["recipeVersion"] != 1
                or recipe["transition"] != transition or recipe["verificationScope"] != "DONOR_ONLY"):
            return None
        proof = recipe["donor"]["acceptedRun"]["sha256"]
        if not re.fullmatch(r"[a-f0-9]{64}", proof):
            return None
        parts = {part["id"]: part for part in recipe["parts"]}
        if len(parts) != len(recipe["parts"]):
            return None
        candidates = []
        ignore = parts.get("legacy-ignore-directory-basenames")
        if ignore and ignore_text is not None:
            patterns = [line for line in ignore_text.splitlines() if line and not line.startswith("#")]
            examples = ignore["verifiedExamples"]
            if patterns and all(re.fullmatch(r"[A-Za-z0-9_-]+", name)
                                and examples.get(name) == f"**/{name}/" for name in patterns):
                candidates.append({
                    "id": ignore["id"], "candidateIgnores": [examples[name] for name in patterns],
                    "probe": "Confirm each pattern targets directories and retains nested matches in ESLint 8, then compare included and ignored paths after migration.",
                })
        catch = parts.get("core-unused-vars-catch-default")
        if catch:
            candidates.append({
                "id": catch["id"], "rule": "no-unused-vars", "candidateOptions": {"caughtErrors": "none"},
                "probe": "Check the effective ESLint 8 rule including inherited configs. Apply only when an unused local variable is reported and an unused catch binding is accepted. Keep all other options.",
            })
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError, RuntimeError, GoalContractError):
        return None
    if not candidates:
        return None
    return {"sha256": expected, "donorReceiptSha256": proof, "verificationScope": "DONOR_ONLY",
            "applicability": "RECIPIENT_PROBE_REQUIRED", "parts": candidates}


def _eslint_test_configs(project: Path, directory: Path) -> tuple[list[dict], bool]:
    observations = []
    incomplete = False
    for subdirectory in ("test", "tests"):
        for filename in _LEGACY_CONFIGS:
            path = directory / subdirectory / filename
            try:
                raw = _read_bytes(path, 64 * 1024, root=project)
                source = raw.decode("utf-8-sig")
            except FileNotFoundError:
                continue
            except (OSError, UnicodeError, GoalContractError):
                incomplete = True
                continue
            observations.append({
                "path": path.relative_to(project).as_posix(), "sha256": hashlib.sha256(raw).hexdigest(),
                "mentionsMochaEnv": bool(_MOCHA_ENV.search(source)),
            })
    return observations, incomplete


def _eslint_card(project: Path, owner: str, section: str) -> dict:
    directory = project / owner
    observations = []
    no_unused_vars_paths = []
    markdown_paths = []
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
        relative_path = path.relative_to(project).as_posix()
        observations.append({
            "path": relative_path,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "format": "flat" if filename in _FLAT_CONFIGS else "eslintrc",
            "mentionsCiEnv": bool(_CI_REFERENCE.search(source)),
        })
        if _NO_UNUSED_VARS_KEY.search(source):
            no_unused_vars_paths.append(relative_path)
        if _MARKDOWN_PROCESSOR.search(source):
            markdown_paths.append(relative_path)

    manifest_path = directory / "package.json"
    script_observations = []
    try:
        raw, manifest = _read(manifest_path, 64 * 1024, root=project)
        manifest_identity = {"path": manifest_path.relative_to(project).as_posix(),
                             "sha256": hashlib.sha256(raw).hexdigest()}
        script_observations = _eslint_script_observations(manifest)
        if len(script_observations) > 8:
            incomplete = True
            script_observations = script_observations[:8]
        if "eslintConfig" in manifest:
            observations.append({**manifest_identity, "format": "eslintrc", "mentionsCiEnv": False})
            eslint_config = manifest["eslintConfig"]
            if isinstance(eslint_config, dict):
                config_text = json.dumps(eslint_config)
                if _NO_UNUSED_VARS_KEY.search(config_text):
                    no_unused_vars_paths.append(manifest_identity["path"])
                if _MARKDOWN_PROCESSOR.search(config_text):
                    markdown_paths.append(manifest_identity["path"])
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError, json.JSONDecodeError, GoalContractError):
        incomplete = True

    ignore_path = directory / ".eslintignore"
    ignore_observation = None
    ignore_text = None
    try:
        raw = _read_bytes(ignore_path, 64 * 1024, root=project)
        ignore_observation = {"path": ignore_path.relative_to(project).as_posix(),
                              "sha256": hashlib.sha256(raw).hexdigest()}
        ignore_text = raw.decode("utf-8-sig")
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError, GoalContractError):
        incomplete = True

    modes = {row["format"] for row in observations}
    test_configs, tests_incomplete = _eslint_test_configs(project, directory) if "eslintrc" in modes else ([], False)
    incomplete |= tests_incomplete
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
    if script_observations:
        guidance += " Review the observed npm scripts for flat-config compatibility before running lint."
        if any("--ext" in row["legacyOptions"] for row in script_observations):
            guidance += " Preserve the original file extensions with matching flat-config files globs."
        if any(row["forcesLegacyConfig"] for row in script_observations):
            guidance += " Check where legacy mode is forced and select the intended mode for each invocation."
        checks.append("Run the affected npm scripts and compare their selected files and exit behavior.")
    if ignore_observation:
        guidance += " Review .eslintignore patterns and preserve applicable exclusions in the flat config."
        checks.append("Check representative included and ignored paths after the config change.")
    if no_unused_vars_paths:
        guidance += (
            " ESLint 9 changes the core no-unused-vars caughtErrors default from none to all. "
            "Check the effective ESLint 8 rule options and keep caughtErrors: none explicitly "
            "if that was the previous behavior and behavior must be preserved."
        )
        checks.append("Compare an unused local variable and an unused catch binding under both ESLint versions.")
    if test_configs:
        guidance += " Check the nested legacy test configs and migrate their effective test-only overrides."
        if any(config["mentionsMochaEnv"] for config in test_configs):
            guidance += " Scope Mocha globals to the test files where ESLint 8 enabled them."
        checks.append("Lint representative test and non-test files to verify the scoped overrides.")
    if markdown_paths:
        guidance += (
            " Preserve the Markdown processor and its virtual-file overrides. "
            "Keep those overrides after broad JavaScript rules so the general settings do not re-enable rules disabled for code fences."
        )
        checks.append("Lint a fenced JavaScript block in Markdown and compare its diagnostics and source lines.")
    if mentions_ci:
        checks.append("Run the same lint probe with CI unset and with CI=true. Compare rule severities in both modes.")
    recipe = _eslint_recipe(project, ignore_text) if not incomplete and modes == {"eslintrc"} and len(observations) == 1 else None
    return {
        **({"recipe": recipe} if recipe else {}),
        "package": ESLINT_TRANSITION[0], "from": ESLINT_TRANSITION[1], "to": ESLINT_TRANSITION[2],
        "owner": owner, "section": section, "status": "REVIEW_REQUIRED",
        "observationScope": "OWNER_CONFIG_DIRECT_NPM_SCRIPTS_AND_COMMON_TEST_CONFIGS",
        "localObservation": "PARTIAL" if incomplete else "OBSERVED",
        "observations": observations,
        "invocationObservations": {**manifest_identity, "scripts": script_observations} if script_observations else None,
        "ignoreObservation": ignore_observation,
        "nestedTestConfigObservations": test_configs,
        "markdownProcessorPaths": markdown_paths,
        "ruleObservations": [{"rule": "no-unused-vars", "paths": no_unused_vars_paths}] if no_unused_vars_paths else [],
        "guidance": guidance,
        "sources": [ESLINT_V9_GUIDE, _CONFIG_GUIDE, _CLI_GUIDE],
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
    return _describe_migrations(project, targets if model_status == "SAT_IN_MODELED_DOMAIN" else [])


def describe_exact_migrations(project: Path, targets: list[dict]) -> dict:
    """Describe explicit transitions without a compatibility assertion."""
    return _describe_migrations(project, targets)


def _describe_migrations(project, targets):
    result = {"schemaVersion": 1, "authority": "RECOMMENDATION_ONLY",
              "status": "NO_SELECTED_TRANSITION", "transitions": [], "uncoveredCount": 0}
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
