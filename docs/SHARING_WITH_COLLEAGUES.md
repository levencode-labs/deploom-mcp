# Sharing Deploom MCP with colleagues

[Русский](SHARING_WITH_COLLEAGUES.ru.md) · [User guide](../USERGUIDE.md) · [Releases](https://github.com/levencode-labs/deploom-mcp/releases/latest)

The easiest first trial is **self-hosted stdio**: each developer installs the published wheel on their machine, registers its Python interpreter in their MCP client, and optionally installs the canonical Skill. The wheel already includes the pinned Core.

Do **not** share a virtual environment, machine-specific agent config, tokens, credentials, caches, state, or project evidence. A copied Windows venv is not portable.

## Offline Python installation kit (Windows)

Prepare a separate kit for each target **Python version, OS and CPU architecture**; dependency wheels (especially solver/SDK components) may be platform-specific. From a clean working directory, with the approved Python index/proxy:

```powershell
$release = '0.2.11'
$bundle = Join-Path $PWD 'deploom-trial'
$house = Join-Path $bundle 'wheelhouse'
New-Item -ItemType Directory -Force $house | Out-Null
$filename = "deploom_mcp-$release-py3-none-any.whl"
$base = "https://github.com/levencode-labs/deploom-mcp/releases/download/v$release"
$wheel = Join-Path $bundle $filename
Invoke-WebRequest "$base/$filename" -OutFile $wheel
Invoke-WebRequest "$base/SHA256SUMS.txt" -OutFile (Join-Path $bundle 'SHA256SUMS.txt')
$line = Get-Content (Join-Path $bundle 'SHA256SUMS.txt') | Where-Object { $_ -match ([regex]::Escape($filename) + '$') }
if (@($line).Count -ne 1) { throw 'Wheel checksum missing or ambiguous' }
$expected = ($line -split '\s+')[0].ToLowerInvariant()
$actual = (Get-FileHash $wheel -Algorithm SHA256).Hash.ToLowerInvariant()
if ($expected -ne $actual) { throw 'Publisher checksum mismatch' }
Copy-Item $wheel (Join-Path $house $filename)
python -m pip download --only-binary=:all: --dest $house $wheel
if ($LASTEXITCODE -ne 0) { throw 'Incomplete wheelhouse: do not distribute' }
Get-ChildItem $house -File | Get-FileHash -Algorithm SHA256 |
  Select-Object @{n='File';e={Split-Path $_.Path -Leaf}}, Hash |
  Export-Csv (Join-Path $bundle 'wheelhouse-sha256.csv') -NoTypeInformation -Encoding UTF8
```

Include this guide and the exact `USERGUIDE.md` revision. The wheelhouse CSV covers retrieved dependency files; it is not an independent publisher signature. Transfer the kit over an approved, authenticated channel. Do not claim a portable install until it passes on an isolated recipient machine.

### Recipient installation

```powershell
# From the unpacked bundle directory:
python -m venv .venv  # Python version must match the wheelhouse
& .\.venv\Scripts\python.exe -m pip install --no-index --find-links .\wheelhouse deploom-mcp==0.2.11
if ($LASTEXITCODE -ne 0) { throw 'Offline install failed' }
& .\.venv\Scripts\python.exe -m pip check
& .\.venv\Scripts\deploom-install-skill.exe --client codex --scope user --dry-run
```

Choose and approve a Skill installation target before running it without `--dry-run`. Register this computer's **own** Python executable in MCP settings using the [user guide](../USERGUIDE.md#3-connect-an-mcp-client). Offline wheel installation does not make registry/advisory planning offline: unavailable evidence must remain `UNKNOWN`.

## Networked MCP (remote HTTP)

Deploom supports a Streamable HTTP endpoint in addition to the default stdio transport. It has **no built-in authentication or per-user path isolation** and must not be made public without an authenticated TLS gateway, access controls, separate instances for mutually untrusted users, and least-privilege project mounts.

See [HTTP deployment](HTTP_DEPLOYMENT.md). A simple HTTP server distributing wheel files is **not** an MCP endpoint. A remote MCP process sees the **server's** filesystem, not local project paths from a colleague's machine.
