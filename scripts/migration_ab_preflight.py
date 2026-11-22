"""Prepare the source/config fixture and balanced schedule without invoking a model."""
import argparse
from collections import Counter
import hashlib
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess

TASKS = ("exact-update", "react-workspace", "eslint-flat-config")
CACHE_MODES = ("cold", "warm")
BASELINE = "8.57.1"
TARGET = "9.39.4"
HELD = "7.0.0"
RULE = "no-unused-vars"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def schedule():
    pairs = []
    for task, cache in itertools.product(TASKS, CACHE_MODES):
        for order in ("AB", "BA"):
            pairs.append({"id": f"{task}-{cache}-{order}", "task": task, "cache": cache,
                          "order": order, "state": "NOT_ASSIGNED"})
    counts = Counter((p["task"], p["cache"], p["order"]) for p in pairs)
    assert len(counts) == 12 and all(n == 1 for n in counts.values())
    return {"status": "PREPARATION_ONLY", "paidSessions": 0, "plannedPairs": pairs}


def command(args, cwd, env, expected=0):
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, timeout=120)
    if result.returncode != expected:
        raise RuntimeError(f"{args[0]} {args[1]} exit {result.returncode}, expected {expected}")
    return result.stdout


def project(path, eslint_version, flat):
    path.mkdir()
    write_json(path / "package.json", {
        "name": "deploom-eslint-config-fixture", "version": "1.0.0", "private": True,
        "scripts": {"lint": "eslint src.js"}, "dependencies": {"is-number": HELD},
        "devDependencies": {"eslint": eslint_version},
    })
    (path / "src.js").write_text(
        'const isNumber = require("is-number");\nmodule.exports = value => isNumber(value);\n',
        encoding="utf-8",
    )
    if flat:
        (path / "eslint.config.cjs").write_text(
            'module.exports = [{files: ["**/*.js"], languageOptions: {sourceType: "commonjs"}, '
            'rules: {"no-unused-vars": "error"}}];\n', encoding="utf-8",
        )
    else:
        write_json(path / ".eslintrc.json", {"root": True, "env": {"node": True, "es2022": True},
                                            "rules": {RULE: "error"}})


def lock_entries(path):
    lock = json.loads((path / "package-lock.json").read_bytes())
    entries = {}
    for slot, row in lock["packages"].items():
        if not slot:
            continue
        url, integrity = row["resolved"], row["integrity"]
        if not url.startswith("https://registry.npmjs.org/") or not integrity.startswith("sha512-"):
            raise ValueError(f"Unpinned npm entry: {slot}")
        entries[slot] = {"version": row["version"], "url": url, "integrity": integrity}
    return entries


def prepare(output):
    work = output / "eslint"
    work.mkdir()
    cache = work / "npm-cache"
    for name in ("user.npmrc", "global.npmrc"):
        (work / name).touch()
    env = {k: v for k, v in os.environ.items()
           if not k.lower().startswith("npm_config_") and k not in ("NODE_OPTIONS", "NODE_PATH")
           and not k.endswith(("_TOKEN", "_KEY", "_SECRET"))}
    env.update(npm_config_registry="https://registry.npmjs.org/", npm_config_cache=str(cache),
               npm_config_userconfig=str(work / "user.npmrc"), npm_config_globalconfig=str(work / "global.npmrc"))
    baseline, reference = work / "baseline", work / "reference"
    project(baseline, BASELINE, False)
    project(reference, TARGET, True)
    inventory = {}
    for name, path, version in (("baseline", baseline, BASELINE), ("reference", reference, TARGET)):
        command(["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"], path, env)
        inventory[name] = lock_entries(path)
        assert inventory[name]["node_modules/eslint"]["version"] == version
        assert inventory[name]["node_modules/is-number"]["version"] == HELD
        command(["npm", "ci", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"], path, env)
        command(["npm", "run", "lint"], path, env)
        original = (path / "src.js").read_bytes()
        (path / "src.js").write_bytes(original + b"\nconst deliberatelyUnused = 1;\n")
        result = command(["node", "node_modules/eslint/bin/eslint.js", "--format", "json", "src.js"],
                         path, env, expected=1)
        assert [m["ruleId"] for row in json.loads(result) for m in row["messages"]] == [RULE]
        (path / "src.js").write_bytes(original)

    assert inventory["baseline"]["node_modules/is-number"] == inventory["reference"]["node_modules/is-number"]
    (reference / "eslint.config.cjs").unlink()
    shutil.copyfile(baseline / ".eslintrc.json", reference / ".eslintrc.json")
    command(["npm", "run", "lint"], reference, env, expected=2)
    for path in baseline, reference:
        shutil.rmtree(path / "node_modules")
    write_json(output / "eslint-report.json", {
        "status": "PASS", "paidSessions": 0, "versions": {"baseline": BASELINE, "target": TARGET},
        "held": inventory["baseline"]["node_modules/is-number"],
        "baselineLockSha256": hashlib.sha256((baseline / "package-lock.json").read_bytes()).hexdigest(),
        "targetLockSha256": hashlib.sha256((reference / "package-lock.json").read_bytes()).hexdigest(),
        "baselineAndReferenceOfflineCi": "PASS", "negativeLintRule": RULE,
        "versionOnlyMigration": "FAIL_AS_EXPECTED", "registryEntries": inventory,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--schedule-only", action="store_true")
    args = parser.parse_args()
    output = args.out.resolve()
    output.mkdir()
    write_json(output / "schedule.json", schedule())
    if not args.schedule_only:
        prepare(output)


if __name__ == "__main__":
    main()
