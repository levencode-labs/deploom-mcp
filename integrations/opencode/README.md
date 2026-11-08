# Deploom MCP + OpenCode

[Русский](README.ru.md) · [User guide](../../USERGUIDE.md) · [Releases](https://github.com/levencode-labs/deploom-mcp/releases/latest)

Deploom provides a **stdio MCP server** and an optional canonical `deploom-upgrade` Skill. The published wheel bundles the pinned Core. There is no separate Desktop requirement.

## Install first

Use the [published-wheel guide](../../USERGUIDE.md#1-install-the-release) and record the absolute Python interpreter path. The same installed environment provides the `deploom-install-skill` executable.

## OpenCode configuration

OpenCode **V1 and V2 have different configuration shapes**. Check the installed OpenCode version and official documentation before editing. Preserve existing model/provider/permission/server entries.

### OpenCode V2 example

Merge into the existing `mcp.servers` object (replace the path):

```jsonc
{
  "mcp": {
    "servers": {
      "deploom": {
        "type": "local",
        "command": ["C:/path/to/Deploom/venv/Scripts/python.exe", "-m", "deploom_mcp"],
        "disabled": false
      }
    }
  }
}
```

For OpenCode V1, use the configuration schema documented for your specific V1 build (`mcp.<name>`/`enabled` rather than V2's `mcp.servers`/`disabled`). Do not copy V2 JSON blindly into a V1 installation.

## Skill and first request

```powershell
& "C:/path/to/Deploom/venv/Scripts/deploom-install-skill.exe" --client opencode --scope user --dry-run
& "C:/path/to/Deploom/venv/Scripts/deploom-install-skill.exe" --client opencode --scope user --verify
```

Restart OpenCode, confirm Deploom is connected, and verify the Skill is actually loaded when requested. The live tool list may depend on the entrypoint and current version; do not assume the historic 0.2.2 count describes all newer releases.

First trial: use a public/synthetic project and request **PLAN_ONLY Yellow**, an actual `get_upgrade_plan` call, owner/workspace coverage, the Blueprint path, blockers, and explicit UNKNOWN evidence. No edits or installation are allowed in that trial. See the [user guide](../../USERGUIDE.md#4-first-request-plan-only) for an example and the [HTTP guide](../../docs/HTTP_DEPLOYMENT.md) for separately secured remote deployment.
