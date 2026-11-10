# Agent + MCP migration comparison

Status: protocol prepared; agent comparison NOT_MEASURED. No agent sessions or
model API calls were launched by the migration-engine qualification. The existing
D1 doctor identifies Codex CLI 0.165.0 and its isolation/JSON flags; account model
access is UNVERIFIED. D1 dry-run passes with explicitly FAKE events. Neither is
a migration performance result.

## Question and primary endpoint

Does the same coding agent, with DepLoom available, reach an independently
accepted migration more reliably or sooner under an equal budget? Compare A
(bare agent) and B (same agent plus packaged MCP and its pinned skill). Report
accepted-within-budget over all assigned, preflight-feasible runs first. Count B
runs that choose not to call MCP. Failed/unknown/timed-out arms remain visible.
Report paired wall time only where both arms pass, alongside full completion
curves and failure reasons; do not rank only successful B runs.

Pin the CLI, exact model identifier, reasoning effort, prompt, client rules,
allowed editing tools, network authority and token/tool/wall budgets. Give both
arms the same official references and codemods. Record tokens, tool calls,
metadata/advisory requests, subprocesses, agent reasoning time, solver time,
install/check/runtime/audit time and MCP setup cost separately.

## Corpus and order

Select public or synthetic tasks before observing results:

| Class | Task requirement | Independent acceptance |
|---|---|---|
| Simple exact update | One explicitly pinned target; one held direct dependency | Exact target, held lock identity, unchanged scope, frozen install, behavior |
| Coupled npm workspace | Exact target plus approved companion lines and multiple held owners | Workspace membership, all owner assignments, held versions, npm placement, checks/runtime |
| Source/config migration | Exact ESLint or Vite transition and explicitly permitted repairs | Original checks preserved, relevant config/API behavior, full final audit if required |

For each task and each cache regime use paired isolated copies of one pinned
source/lock/toolchain/registry snapshot. Use balanced AB and BA order in separate
pairs; choose and record order before starting. Use enough repetitions to show
variation rather than treating one pair as evidence of general superiority.
Cold and warm are separate strata. Warm uses equal independently copied caches;
no arm receives the other arm's outputs, credentials, cache mutations or answers.
Registry/advisory/document contents must have frozen identities and equivalent
routing in both arms; an uncontrolled live registry is not a pinned experiment.

## Preflight and independent oracle

Before model calls, verify seed/source/lock hashes, authorized egress, exact
runtime/package-manager identities, artifact integrity and frozen install in
both sandboxes. Confirm the oracle can detect held-dependency drift, changed
checks, an exact-target substitution, incomplete audit and unsupported scope.
Mark infeasible tasks before assignment. After assignment, preserve all failures
rather than retrying or dropping one arm to improve the result.

The oracle must independently re-read the final tree and verify allowed edits,
exact targets, held declarations/versions, frozen installation, the original
check profile, affected runtime and complete audit evidence when required.
MCP SAT, native resolver success and an agent's success message are not oracle
PASS. An already satisfied target is not a completed migration.

## Current harness boundary

`scripts/d1_pilot.py doctor` and `dry-run --out <new-directory>` are no-model
checks and have been run locally. The existing D1 paid runner is a legacy,
fixed-order Chalk6 protocol. It is not the protocol above: it uses a live
registry, A-before-B ordering and its own admission/early-stop rules. Do not
reinterpret its fake events or a single old pair as a migration-engine win.

A real crossover run needs pinned seed/registry/cache artifacts and a runner
implementing the balanced schedule and all-assigned accounting above. Its model
sessions and cost need their own launch decision. This implementation delivers
component measurements and installed-package correctness qualification; it
does not claim that this future experiment has been executed.
