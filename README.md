<div align="center">

<img src="assets/deploom-mcp-mark.png" width="112" alt="Deploom MCP logo" />

# Deploom MCP

**Explainable dependency upgrades for coding agents.**

[Levencode Labs](https://github.com/levencode-labs) · [Русский](README.ru.md) · [User guide](USERGUIDE.md) · [Documentation](docs/INDEX.md) · [Releases](https://github.com/levencode-labs/deploom-mcp/releases)

</div>

Deploom MCP connects a headless dependency-planning engine to MCP-compatible coding agents. It helps make upgrade scope, version choices, compatibility constraints, and missing evidence explicit, so an agent can act on a structured plan instead of guessing what an update means.

The published wheel bundles the **exact pinned [Deploom Core](https://github.com/levencode-labs/deploom-core)**, schemas, and canonical `deploom-upgrade` Skill. Electron/Desktop and a separate Core checkout are not required to use the wheel.

## What you get

- **Planning:** `get_upgrade_plan` returns an inspectable Upgrade Blueprint with authorized scope, target authority, proposed choices, conflicts, and remaining unknowns.
- **Explanation:** `evaluate_assignment` and `explain_conflict` inspect an existing decision and its evidence; they do not silently re-solve or execute an alternative migration.
- **Workspace visibility:** dedicated inventory and bounded joint-planning paths have their own coverage and authority limits. An observed workspace is not automatically an executable workspace migration.
- **Optional Skill:** `deploom-upgrade` describes a disciplined agent workflow: preflight, plan, optional isolated changes, project checks, and evidence report.
- **Separate execution route:** explicitly authorized standalone npm goals can use isolated verification and final checkpoints subject to the [documented support matrix](docs/GOAL_EXECUTION_SUPPORT.md). Imported plans do not confer execution authority.

**`PLANNING_ONLY` is not a successful upgrade.** An npm exit code, lockfile-only result, installed dependency tree, project tests, runtime checks, and final policy audit provide different kinds of evidence.

## Quick start

1. [Install a published wheel and optional Skill](USERGUIDE.md#1-install-the-release) (or [на русском](USERGUIDE.ru.md#1-установите-релиз)).
2. [Connect your MCP client](USERGUIDE.md#3-connect-an-mcp-client): [Codex](integrations/codex/README.md), [OpenCode](integrations/opencode/README.md), or [Claude Code](integrations/claude-code/README.md).
3. Start with a **plan-only** request on a small public/synthetic project; review the actual Blueprint and scope coverage.
4. Authorize isolated edits and independent verification separately if you want the agent to perform a migration.

For HTTP/container use, see [Streamable HTTP deployment](docs/HTTP_DEPLOYMENT.md). The default local transport is **stdio**. **Remote HTTP has no built-in user authentication or per-user project isolation**; do not expose it without an authenticated gateway and least-privilege isolation.

## Develop from source

Python **`>=3.12,<3.15`** is declared; **Python 3.12** is the recommended first-test interpreter. Native Windows and Linux use platform-specific wheels for some dependencies.

```bash
git clone https://github.com/levencode-labs/deploom-mcp.git
cd deploom-mcp
git submodule update --init --recursive
python -m venv .venv
# Activate the venv or invoke its Python explicitly:
python -m pip install -e .
python -m deploom_mcp
```

The last command starts a protocol server on stdio, **not** a web UI. For reproducible installed-wheel trials, follow the [user guide](USERGUIDE.md), not an editable source install. The Core gitlink is pinned and must not be rewritten as part of a cosmetic/docs change.

## Explore

| Area | Where to start |
| :-- | :-- |
| Installation and first request | [User guide](USERGUIDE.md) · [Руководство](USERGUIDE.ru.md) |
| Technical contracts | [Documentation index](docs/INDEX.md) |
| Agent workflow | [Canonical Skill](skills/deploom-upgrade/SKILL.md) |
| Execution/evidence limitations | [Goal support matrix](docs/GOAL_EXECUTION_SUPPORT.md) |
| Remote transport and security | [HTTP deployment](docs/HTTP_DEPLOYMENT.md) |
| Sharing an installation | [Colleague guide](docs/SHARING_WITH_COLLEAGUES.md) |

**Release reference:** [`v0.2.11`](https://github.com/levencode-labs/deploom-mcp/releases/tag/v0.2.11) was the latest published release verified on 2026-11-08. Use [latest releases](https://github.com/levencode-labs/deploom-mcp/releases/latest) for subsequent versions. Neither a plan nor a release tag is a claim of universal migration quality or speedup.
