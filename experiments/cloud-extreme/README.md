# DepLoom Extreme Upgrade Challenge P3 — Codex Cloud

Status: **engineering experiment, no model calls or actual migration acceptance yet**.
This directory is an independent benchmark harness proposal, not a change to
Deploom release or to any upstream project. A signed knowledge-record match is
advisory and never grants execution/verification authority.

## Fixed cases

See [cases.json](cases.json) for source commit IDs, package-manager families,
targets and candidate tests. Catalog targets are proposed challenges: first
prove the original checkout is green, the desired target version exists in
the approved public registry and the full frozen original test scope is known.

| Case | PM | Classification |
|---|---|---|
| undici-node24 | npm | standalone npm, real independent tests required |
| playwright-node24 | npm workspace | MCP planning + agent execution, no blanket MCP workspace execution claim |
| npm-cli-tap21 | npm workspace | MCP planning + agent execution, no blanket MCP workspace execution claim |
| fluentui-ts6-vite8 | Yarn Berry | exploratory; MCP final execution unsupported |
| storybook-vite8 | Yarn Berry | exploratory; MCP final execution unsupported |
| backstage-eslint9 | Yarn Berry | exploratory; MCP final execution unsupported |
| n8n-pnpm-boundary | pnpm | negative control only, no upgrade PASS |

No narrowing the matrix after a FAIL. Distinguish unsupported and UNKNOWN.
Each arm operates on an independent clean checkout at the same exact SHA.

## 0. Signed shared knowledge gate — BEFORE paid runs

Pin the public [MCP release v0.2.27](https://github.com/levencode-labs/deploom-mcp/releases/tag/v0.2.27).
Official wheel SHA-256:

    5bd42bb881ca4ccb8694694ba1a4819eb8e48ec807f4bc4c40bbb5498dd630bb

From the experiment branch in a Linux Codex Cloud task with Python 3.12:

~~~bash
set -euo pipefail
ROOT="$HOME/deploom-p3-private"
mkdir -p "$ROOT/dist" "$ROOT/receipts" "$ROOT/records"
chmod 700 "$ROOT"
curl --fail --location --retry 2 --proto '=https' --proto-redir '=https' \
  https://github.com/levencode-labs/deploom-mcp/releases/download/v0.2.27/deploom_mcp-0.2.27-py3-none-any.whl \
  -o "$ROOT/dist/deploom_mcp-0.2.27-py3-none-any.whl"
echo "5bd42bb881ca4ccb8694694ba1a4819eb8e48ec807f4bc4c40bbb5498dd630bb  $ROOT/dist/deploom_mcp-0.2.27-py3-none-any.whl" | sha256sum --check

python3.12 -m venv "$ROOT/venv"
"$ROOT/venv/bin/python" -m pip install --no-input "$ROOT/dist/deploom_mcp-0.2.27-py3-none-any.whl"
unset DEPLOOM_MCP_LOCAL_CACHE_ONLY DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE
"$ROOT/venv/bin/python" experiments/cloud-extreme/knowledge_gate.py \
  --wheel "$ROOT/dist/deploom_mcp-0.2.27-py3-none-any.whl" \
  --records-dir "$ROOT/records" \
  --out "$ROOT/receipts/pages-global-01.json"
~~~

The script confirms the exact public wheel digest and all installed Python
package file hashes from wheel RECORD, refreshes the signed public knowledge
cache, and uses a REAL installed MCP stdio session to query the control:

    tool = get_shared_migration_evidence
    package = react-dom
    from_version = 18.2.0
    to_version = 18.3.1
    snapshotSource = verified-pages
    status = found
    recipes contain align-react-renderers

The MCP validates an Ed25519 signature over the public snapshot with the
installed fixed public key. Sources:
[cache.json](https://levencode-labs.github.io/deploom-npm/v1/cache.json),
[cache.sig](https://levencode-labs.github.io/deploom-npm/v1/cache.sig),
[shared_records.py](../../src/deploom_mcp/shared_records.py).

**Stop on exit 2 or anything but PASS_VERIFIED_PAGES.** A bundled recipe is
NOT a verified external-source receipt. If source=bundled, explicitly report
EXTERNAL_CACHE_UNVERIFIED, even when the recipe is present. Cold or warm npm
cache, Core planning metadata and OSV are separate and are not substitutes.

Network allowlist: GitHub Releases and approved asset redirect hosts, the
GitHub Pages hostname, approved package registries and Python index. Do not
work around proxy/auth restrictions. Do not log secrets or use a private build.

The shared knowledge cache is stored under the supplied records directory;
the default outside this harness is ~/.cache/deploom/records.
MCP planning packuments normally live at
DEPLOOM_MCP_STATE_DIR/registry-metadata-cache, default state
~/.deploom-mcp. Core planning cache defaults to
~/.cache/deploom/planning-v1. Native npm uses its own _cacache.

## 1. Every migration gets an exact-version signed lookup

For a case with exact from/to declarations, e.g. the undici control:

~~~bash
"$ROOT/venv/bin/python" experiments/cloud-extreme/case_knowledge.py \
  --case undici-node24 --source "$ROOT/checkouts/undici-node24" \
  --gate "$ROOT/receipts/pages-global-01.json" \
  --wheel "$ROOT/dist/deploom_mcp-0.2.27-py3-none-any.whl" \
  --records-dir "$ROOT/records" \
  --out "$ROOT/receipts/undici-node24-knowledge.json"
~~~

This checks the source Git SHA, frozen lockfile SHA-256, the public wheel and
the SAME signed Pages control, then asks MCP for each exact target. The
receipt includes snapshotSource, status, matchedRecord, recipes, observations,
provenance and initial planImpact=NOT_EVALUATED. It never calls a model.

For a declaration range such as ^8.6.0, resolve the REAL installed version
from the pinned lockfile and the selected exact target from registry metadata.
Do not invent a version from the range minimum. Supply the separately checked
resolution:

~~~json
{
  "schema": "deploom-exact-transitions-v1",
  "caseId": "backstage-eslint9",
  "transitions": [
    {"package": "eslint", "fromVersion": "REPLACE_WITH_EXACT_SEMVER", "toVersion": "9.39.4"}
  ]
}
~~~

Pass the file through --resolutions to case_knowledge.py. The receipt marks
operator-reported resolution as NEEDS_ORACLE; independent native lock/tree
verification must still establish that version.

For each exact queried transition:
- verified-pages + found = matched, advisory knowledge; report recipes and
  actual use in plan separately
- verified-pages + no-matching-record = normal for a new migration, NOT a FAIL
- bundled, other source, malformed or missing response = NO_GO for cache gate
- unresolved range = NOT QUERIED; do not relabel it no-matching-record

## 2. Agent comparison and cache isolation

Use the published Cloud environment as a coordinator, not as either measured
agent. It starts isolated API-billed agent processes only with explicitly
selected API credentials, model and effort, after the gates and original
baseline pass. A normal Codex Cloud task consumes Codex usage, NOT
automatically credits belonging to an OpenAI API project. Before a paid block,
verify API project attribution on a tiny, explicitly metered smoke request.
Keep credentials in restricted Cloud secret settings; do not put any API key
or third-party auth in a repository, transcript or public artifact.

Run A = Bare Agent without MCP and B = MCP cold. Then run C = MCP warm on a
NEW clean checkout using the metadata cache produced by B. The public signed
knowledge edition should be held equal for B and C. Keep npm download cache
states matched between arms, otherwise npm download speed contaminates the
MCP cache measurement. Add a same-MCP zero-TTL control to isolate the metadata
cache. Use balanced order/replications; no reruns of a failed run for a
better-looking status.

Preserve frozen source, toolchain and checks; original tests, install,
lint/typecheck, owner/provider resolution and independent runtime oracle.
Store wall time by stage, HTTP GET and 304 counts, registry hit/miss,
total model input/output/cached tokens, actual API project usage, exact
lockfile delta, source repairs, independent test outcomes and reason for
UNKNOWN. Prompt-cache tokens are NOT Deploom knowledge records.

Codex Cloud starts each NEW task with its own workspace: cold/warm comparison
must stay within an isolated repeatable workspace, or an explicitly rehydrated
artifact. To retain useful state after a subscription downgrade, check
configs/bootstrap, source SHA, tool versions, logs, receipts and immutable
cache snapshots into a separate reviewed GitHub evidence destination.
Do not upload secrets or raw private agent transcripts. No upstream pushes,
global config changes, auto merges or publishing.

## 3. Record how knowledge influenced each plan, not merely whether found

After each paid agent run, a human/independent reviewer fills one record for
EVERY exact migration transition, including no-matching-record:

| Impact | Required interpretation |
|---|---|
| NO_MATCH | Verified signed source returned no-matching-record |
| NOT_SHOWN | There was a record but no evidence it was shown to the agent |
| CONSIDERED_NO_CHANGE | Agent saw it but no plan change is attributable |
| PLAN_GUIDED | Evidence links recipe to a change in the actual plan |
| UNKNOWN | Unable to establish influence |

Before agent work all entries remain NOT_EVALUATED and shownToAgent=false.
Never infer that a found record was followed automatically.

Input to impact_review.py (FORMAT EXAMPLE, not a claim about actual results):

~~~json
{
  "schema": "deploom-plan-impact-v1",
  "caseId": "undici-node24",
  "entries": [
    {"package": "@types/node", "shownToAgent": false,
     "impact": "NO_MATCH", "rationale": "Only if the signed MCP lookup confirmed no match"}
  ]
}
~~~

Run after reading the actual case knowledge receipt:

~~~bash
"$ROOT/venv/bin/python" experiments/cloud-extreme/impact_review.py \
  --lookup "$ROOT/receipts/undici-node24-knowledge.json" \
  --claims "$ROOT/review/undici-node24.json" \
  --out "$ROOT/receipts/undici-node24-impact.json"
~~~

For PLAN_GUIDED also supply --plan-before, --plan-after, --agent-trace
paths, with a nonempty rationale. The script binds their SHA-256 and rejects
a guided claim when MCP returned no-matching-record. Review status remains
REVIEWED_CLAIMS_NOT_CAUSAL_PROOF: a causal agent uplift must be shown by the
separately controlled experiment, not a single narrative report.

## 4. Phased five-day programme

1. Public wheel, signed Pages gate, first independent baseline checks
2. Undici and Playwright
3. npm CLI and balanced repeated cold/warm blocks
4. Fluent UI and Storybook as explicitly unsupported-Yarn exploratory cases
5. Backstage and pnpm negative control, final evidence and economics

The repository scripts only set up evidence and verify data; no script here
silently starts expensive API calls. The researcher must use an explicitly
controlled Cloud agent harness/CLI to start measured model sessions after
the preflight and baseline. Local negative-control tests cost no tokens:

~~~bash
python -m unittest discover -s experiments/cloud-extreme -p 'test_*.py' -v
~~~

Launch the next cloud task via Work in > Cloud, choose the already published
Levencode Labs environment and the experimental MCP branch. First perform
Phase 0 with ZERO model/API calls inside the harness, then only prepare frozen
baselines. Do not claim a live Pages verification until its receipt exists.
