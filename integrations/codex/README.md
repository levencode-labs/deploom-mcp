# Deploom MCP + Codex

[User guide](../../USERGUIDE.md) · [Canonical Skill](../../skills/deploom-upgrade/SKILL.md) · [Deploom MCP releases](https://github.com/levencode-labs/deploom-mcp/releases/latest)

This document covers **registration and discovery**, not the upgrade algorithm. The same `deploom-upgrade` Skill is used across supported clients; no Codex-specific copy of its behavior is maintained here.

## Install a published wheel first

Follow the [release-wheel installation guide](../../USERGUIDE.md#1-install-the-release). Confirm that the Python interpreter you will register imports the expected `deploom-mcp` package. The wheel bundles Core, its schema and the canonical Skill.

## Connect a local stdio server

Use Codex CLI's MCP command with the **absolute** Python path from the installed wheel environment:

```powershell
codex mcp add deploom -- "C:/path/to/Deploom/venv/Scripts/python.exe" -m deploom_mcp
```

Or edit your existing `~/.codex/config.toml` without overwriting other MCP servers:

```toml
[mcp_servers.deploom]
command = "C:/path/to/Deploom/venv/Scripts/python.exe"
args = ["-m", "deploom_mcp"]
```

`codex mcp --help` and `codex --version` are authoritative for the options available in your installed Codex CLI version. This documentation does not assume that an old recorded CLI build remains current.

## Install the canonical Skill (optional)

From the same installed environment:

```powershell
& "C:/path/to/Deploom/venv/Scripts/deploom-install-skill.exe" --client codex --scope user --dry-run
& "C:/path/to/Deploom/venv/Scripts/deploom-install-skill.exe" --client codex --scope user --verify
```

For project scope, use the installer-documented `--scope project --project /absolute/path` options. A Skill install is not an MCP registration and does not prove a running Codex session actually loaded that Skill. Start a new session and verify.

## Minimal non-destructive check

Ask Codex to load `deploom-upgrade`, inspect connected MCP tools, and call `get_upgrade_plan` against a **public/synthetic project** in `PLAN_ONLY`. Verify it reports a real Blueprint path, planning scope, unresolved evidence and no project edits. Use a read-only sandbox for the trial; denied access is a real `BLOCKED`, not permission to weaken the sandbox. For changes and runtime acceptance, authorize an isolated execution separately.

See the [user guide](../../USERGUIDE.md#4-first-request-plan-only) and the [execution support matrix](../../docs/GOAL_EXECUTION_SUPPORT.md).
