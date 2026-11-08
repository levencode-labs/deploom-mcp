# Deploom MCP distribution and verification

[Русский](DISTRIBUTION.ru.md) · [User guide](../USERGUIDE.md) · [Releases](https://github.com/levencode-labs/deploom-mcp/releases/latest)

Published wheel artifacts bundle the pinned [Deploom Core](https://github.com/levencode-labs/deploom-core), Upgrade Blueprint schemas, canonical Skill and installer. Installed-wheel users do **not** need Desktop/Electron, a separate Core checkout, or Git to run the server.

`release-manifest.json` records expected version, exact Core/gitlink identity, source content inventory and canonical Skill/schema bindings. These records must be verified against actual shipped bytes and runtime provenance; neither a manifest nor an installed Skill receipt proves which instructions an agent has loaded.

## Build from the pinned source checkout

Use a clean checkout, an isolated Python 3.12–3.13 environment and the approved dependencies/tools. **Do not modify the Core submodule in place.** Only an explicitly reviewed Core commit can become a new pin.

```bash
git submodule update --init --recursive
python scripts/check_skill.py --write
python scripts/update_release_manifest.py
python scripts/check_skill.py
python scripts/update_release_manifest.py --check
python -m build --no-isolation
```

These commands may rewrite generated integrity metadata; run them only in a reviewed release change with a known baseline. A cosmetic README/logo update **must not** change the Core gitlink, package version, `release-manifest.json`, or canonical Skill.

## Install or test the current release

Version shown here is a **pinned example** verified as published on 2026-11-08:

```bash
python -m pip install dist/deploom_mcp-0.2.11-py3-none-any.whl
python -m pip check
python scripts/smoke_stdio.py
python scripts/smoke_wheel.py dist/deploom_mcp-0.2.11-py3-none-any.whl
```

Use the actual newly built filename for future versions. The smoke wheel test uses an isolated environment and verifies bundled resources and basic startup; it does **not** validate a migrated application, general performance, or a new version's release acceptance. Also test a wheel rebuilt from the sdist and record hashes/toolchain versions for release evidence.

## Release boundary

Release publishing requires the exact commit, tag, CI matrix, source sanitation and server-side artifact digest checks documented in [Releasing](RELEASING.md). A local build does not publish to GitHub or PyPI. Do not move a published tag, replace released asset bytes, or silently broaden an approved execution matrix.
