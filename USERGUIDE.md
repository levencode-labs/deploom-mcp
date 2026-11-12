# Deploom MCP — User guide

[Русский](USERGUIDE.ru.md) · [Product overview](README.md) · [Documentation](docs/INDEX.md) · [Latest release](https://github.com/levencode-labs/deploom-mcp/releases/latest)

This guide describes a **local MCP client + released wheel** workflow. It does not require Deploom Desktop, a source checkout, or a separate Core installation. First use a public/synthetic project; decide separately whether your private project and its dependency inventory may contact the configured services.

> **Release pinned in examples:** Deploom MCP **0.2.19**, including canonical Skill contract **2.0.27**. For newer releases, use the exact files and checksums from [GitHub Releases](https://github.com/levencode-labs/deploom-mcp/releases/latest) rather than mixing versions.

## 1. Install the release

**Requirements:** Python `>=3.12,<3.15` is declared; **CPython 3.12 x86-64** is a practical starting point on Windows/Linux. Package dependencies (including native wheels) must be available from an approved Python index or offline wheelhouse. You also need the appropriate Node/npm toolchain to verify a JavaScript project later.

### Windows (PowerShell)

```powershell
$deploomHome = Join-Path $env:LOCALAPPDATA 'Deploom'
python -m venv "$deploomHome\venv"  # Use a Python 3.12 interpreter
$py = Join-Path $deploomHome 'venv\Scripts\python.exe'
$wheel = 'deploom_mcp-0.2.19-py3-none-any.whl'
$base = 'https://github.com/levencode-labs/deploom-mcp/releases/download/v0.2.19'
New-Item -ItemType Directory -Force "$deploomHome\downloads" | Out-Null
Invoke-WebRequest "$base/$wheel" -OutFile "$deploomHome\downloads\$wheel"
Invoke-WebRequest "$base/SHA256SUMS.txt" -OutFile "$deploomHome\downloads\SHA256SUMS.txt"
$entry = Get-Content "$deploomHome\downloads\SHA256SUMS.txt" | Where-Object { $_ -match ([regex]::Escape($wheel) + '$') }
if (-not $entry -or @($entry).Count -ne 1) { throw 'Missing/ambiguous wheel checksum' }
$expected = ($entry -split '\s+')[0].ToLowerInvariant()
$actual = (Get-FileHash "$deploomHome\downloads\$wheel" -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw 'Release checksum mismatch' }
& $py -m pip install "$deploomHome\downloads\$wheel"
if ($LASTEXITCODE -ne 0) { throw 'Installation failed' }
& $py -m pip check
& $py -c "import importlib.metadata as m; print(m.version('deploom-mcp'))"
```

If `python` is not the intended version, invoke your installed Python 3.12 binary explicitly. The release checksum verifies agreement with the publisher's `SHA256SUMS.txt`; it is **not** an independent signature. For Python dependencies, follow your approved index, certificates, and proxy rules.

### Linux (Python 3.12)

```bash
python3.12 -m venv "$HOME/.local/share/deploom/venv"
PY="$HOME/.local/share/deploom/venv/bin/python"
"$PY" -m pip install 'https://github.com/levencode-labs/deploom-mcp/releases/download/v0.2.19/deploom_mcp-0.2.19-py3-none-any.whl'
"$PY" -m pip check
"$PY" -c 'import importlib.metadata as m; print(m.version("deploom-mcp"))'
```

For a checksum-verified offline distribution to colleagues, see [Sharing with colleagues](docs/SHARING_WITH_COLLEAGUES.md). Published wheels bundle Core; editable source clones are for developers, not required for a first trial.

## 2. Install the optional Skill

The same versioned **`deploom-upgrade`** Skill supports different coding clients. The installer does **not** register MCP or alter a client's existing configuration.

```powershell
# Windows example, continuing from step 1
& "$deploomHome\venv\Scripts\deploom-install-skill.exe" --client codex --scope user --dry-run
& "$deploomHome\venv\Scripts\deploom-install-skill.exe" --client codex --scope user --verify
```

Choose `--client opencode` or `--client claude-code` instead when appropriate; use `--scope project --project /absolute/project/path` for a project-level installation. On Linux, invoke `$HOME/.local/share/deploom/venv/bin/deploom-install-skill` instead of the Windows `.exe`.

If an older installed Skill differs, back it up, inspect `--dry-run`, and use a documented explicit replacement flow (`--force --verify`) only after reviewing it. Avoid editing the installed hash-bound Skill files in place. Restart or start a new coding-client session and verify that the client loaded the intended Skill content.

## 3. Connect an MCP client

### Codex

Use the **absolute interpreter path** from the wheel environment, not an arbitrary system Python:

```powershell
codex mcp add deploom -- "$deploomHome\venv\Scripts\python.exe" -m deploom_mcp
```

Alternatively configure your existing `~/.codex/config.toml` according to your Codex version (preserving other entries):

```toml
[mcp_servers.deploom]
command = "C:/path/to/Deploom/venv/Scripts/python.exe"
args = ["-m", "deploom_mcp"]
```

See the [Codex integration notes](integrations/codex/README.md) and check `codex mcp --help` on your installed CLI. Do not disable the sandbox or broaden project trust just to make a test pass.

### OpenCode V2

Merge the following server entry into your **existing** OpenCode configuration; replace the path and keep all unrelated configuration:

```jsonc
{
  "mcp": {
    "servers": {
      "deploom": {
        "type": "local",
        "command": [
          "C:/Users/YOUR_USER/AppData/Local/Deploom/venv/Scripts/python.exe",
          "-m", "deploom_mcp"
        ],
        "environment": {
          "DEPLOOM_MCP_STATE_DIR": "C:/Users/YOUR_USER/AppData/Local/Deploom/state"
        },
        "disabled": false
      }
    }
  }
}
```

For Linux use the equivalent absolute `venv/bin/python`. Verify the configuration format for **your installed OpenCode version**; V1 and V2 differ. See [OpenCode integration](integrations/opencode/README.md).

### Other MCP-compatible clients

Configure a **stdio** server with command `/absolute/path/to/venv/python` and arguments `-m deploom_mcp`. Restart the client, inspect the actual MCP tools catalog and confirm there is no startup error. A terminal running `python -m deploom_mcp` expects protocol input; it is not a chat interface or website.

## 4. First request: plan only

Use a public/synthetic project first. Example agent request:

> Load `deploom-upgrade` and use the installed Deploom MCP. PLAN_ONLY, target Yellow. Do not change project files, install packages, commit, push or publish. Inventory workspaces; state actual plan coverage and selected mutable scope. Call `get_upgrade_plan` rather than only describing it. Report the real Blueprint path, proposal status, blockers and UNKNOWN evidence. Do not infer runtime success from planning.

A minimal direct request to `get_upgrade_plan`:

```json
{
  "project_path": "C:/projects/example-app",
  "target_level": "yellow",
  "min_lag_ok_pct": 80,
  "target_mode": "advisory",
  "objective": "minimum-change-to-policy",
  "deadline_seconds": 120
}
```

Use only the project-approved registry; the `registry` parameter takes a URL **without credentials**. Read the complete Blueprint, not only an agent summary. A proposal may have partial coverage, unresolved peers, candidate caps, incomplete evidence, or stale input identity.

### How to interpret the result

| Result or observation | What it proves |
| :-- | :-- |
| `PLANNING_ONLY` / `NOT_VERIFIED` | A plan was computed; no installation or project acceptance is established. |
| Feasible modeled assignment | Satisfies the represented constraints, **not** necessarily the real npm tree. |
| Lockfile-only success | The lock representation passed its declared checks; physical providers and runtime remain unobserved. |
| `npm ci`, `npm ls`, actual owner/provider checks | Evidence about a specific installed dependency tree, not a general application proof. |
| Build/tests/runtime gates | Only the checks actually executed, on the exact candidate and platform. |
| `UNKNOWN`, `PARTIAL`, `NOT_RUN` | Missing work or evidence must remain visible; never convert them to PASS. |

For npm workspaces, inventory and joint plans have different scopes. A held dependency, peer provider, or other owner's declaration cannot be changed just to make an independent plan look green. Use the [current execution support matrix](docs/GOAL_EXECUTION_SUPPORT.md) before requesting the host-authorized goal route.

## 5. Authorize an upgrade separately

After reviewing the plan, give an explicit scope and authorization. Example:

> Apply only the agreed dependency changes in an isolated worktree. Preserve all unrelated manifests, lockfile relationships and source. Run a fresh install, verify actual dependency ownership/peer providers, execute the required project tests and runtime checks, and perform an independent final audit. Show changed files, results and missing evidence. Do not commit, push or release.

The agent-managed edit workflow and the explicit host-authorized MCP goal route are **not interchangeable**. `get_goal_plan`, `verify_goal_candidate`, and `apply_goal_candidate` have separate capabilities and evidence requirements; imported plans or agent prose do not grant write authority. See [Goal execution support](docs/GOAL_EXECUTION_SUPPORT.md).

## 6. Network and security

**Local stdio is not the same as offline operation.** The planner may contact npm registry endpoints and OSV (`api.osv.dev` by default), and optional reference sources. Sending a registry URL does not reroute OSV queries. A registry-backed advisory path may send package names and versions in requests. Check data policy before using private dependencies, and retain `UNKNOWN` if approved evidence sources are unavailable.

**Remote Streamable HTTP** is documented in [HTTP deployment](docs/HTTP_DEPLOYMENT.md). It has no built-in user authentication or per-user project-path isolation. For remote access, require a TLS/authenticated gateway, least-privilege mounts, separated instances for untrusted users, and appropriate resource/process controls. The `project_path` refers to the server's filesystem, not the client's machine.

For proxy and registry problems, see [System proxy](docs/SYSTEM_PROXY.md) and the project's current data-policy documentation. Never silently fall back to an unapproved public registry or advisory endpoint.

## Troubleshooting

- **MCP not visible:** inspect the exact Python path, installed package version, client config and stderr, then restart the client.
- **Skill not loaded:** install/verify the Skill *and* connect MCP; these are separate steps.
- **Registry / certificate / proxy failure:** keep incomplete evidence `UNKNOWN`, fix the approved route, and retry with a bounded deadline.
- **Monorepo partially covered:** report which owners/workspaces were not planned; do not assert full-tree acceptance.
- **Planner returns a proposal:** verify the actual candidate with fresh package-manager and project checks before accepting Yellow/Green.

[Documentation index](docs/INDEX.md) · [Current release](https://github.com/levencode-labs/deploom-mcp/releases/latest)
