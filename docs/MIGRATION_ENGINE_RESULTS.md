# Migration engine: implementation and qualification

2026-11-11. Local engineering candidate on `codex/migration-engine` in Core and
MCP. Implementation and selected package qualification are complete. Publication,
installation into the user's active MCP and an agent A/B performance claim are
not part of this result. Existing public v0.2.13 has not been replaced.

## Delivered behavior

- Workspace discovery prunes paths that cannot match admitted workspace patterns.
  It retains selected-path link/reparse checks, entry/directory budgets, repeated
  reads and independent lock owner reconciliation.
- Candidate-local evidence gaps no longer discard an otherwise proven assignment.
  All original variables/ranks remain in the model; unknown candidates are
  forbidden in the proven subdomain. Exhaustion with partial evidence is UNKNOWN,
  not a proof that the original goal is impossible. Exact and held inputs remain
  fixed. Compact responses and scoped details expose the evidence boundary.
- A request prepares metadata, peer constraints and health evidence once.
  Native retries solve the same snapshot with exact-vector exclusions; npm text
  alone does not create structural learned constraints.
- Public independent metadata may use four isolated workers per request. Private
  or context-dependent sessions stay sequential. Cookie/no-store responses cause
  speculative results to be discarded and replayed in the original session.
  Workers do not publish late results into the parent cache after a deadline.
- Registry advisory matching batches npm-semver comparisons using the same native
  engine/options. Strict result identities and bounded process capture preserve
  UNKNOWN on malformed, truncated or timed-out batches.
- `get_project_capabilities` performs local route discovery without fetching or
  executing commands. `get_upgrade_plan` v3 accepts the authorized goal and scope,
  binds inventory internally and selects bounded candidates. Explicit v2 remains
  available. Ambiguous scope returns selectors; a satisfied exact target is
  distinguished from an upgrade; truncated exhaustion recommends a new expanded
  search. Unselected declarations and lock versions stay held.
- Official migration references now include ESLint 8.57.1 to 9.0.0 in addition to
  the two existing exact transitions. Suggested checks have source IDs, NOT_RUN
  status and UNKNOWN local applicability; no automatic codemod is claimed.
- Packaged skill 2.0.15 uses the shorter v3 route when supported and avoids an
  extra capabilities call when the route is already established.

One discovery tool was added; planning was extended through the existing tool.
A separate solve tool, universal trial executor and general semantic impact tool
were deferred. They would require additional execution/evidence contracts.

The base already contained unpublished root npmrc binding and scoped audit
routing/coverage fixes. Those were preserved, not reimplemented or attributed to
the optimizations above. New audit regressions verify scoped lag denial before
any egress and rejection of same-origin redirects to a different audit path.

## Measured component results

| Experiment | Prior/reference | New | What was checked |
|---|---:|---:|---|
| Discovery, 1800 irrelevant directories | 1.623 s; 3610 scandir | 0.0376 s; 4 scandir | Identical inventory digest |
| Discovery, 2100 irrelevant directories | Directory limit reached | Complete, 0.0369 s | Selected membership still complete |
| 100 unique npm-semver comparisons | Median 8.355 s; 100 Node processes | Median 0.125 s; 1 process | All 100 answers identical |
| Eight public metadata reads, local receiver with 100 ms delay | Median 0.890 s; peak concurrency 1 | Median 0.224 s; peak concurrency 4 | Same eight responses/requests |

Semver and metadata each use three runs per arm with balanced order. Discovery
is one run per size/implementation, not a distribution estimate. Measurements
are synthetic local component timings; network-delay input is artificial.
They do not measure agent reasoning, end-to-end migration time or general ROI.
Run `scripts/benchmark_migration_primitives.py --help` for the reproducible runner.
Raw result: `../migration-checks/component-timings.json` relative to this repo.

## Final package qualification

- Core revision: `36586b7abd91a4be17e96d9a60d6fe7626810aa0`.
- Final local wheel SHA-256:
  `800b52a2dbe9ae0385b3c5312f520efcfe4893162ba99c14ade57b9ef8e9f66a`.
- Package metadata still says 0.2.13; this is a private engineering candidate,
  not the published wheel. A release needs a new version and normal release gates.
- Built offline with setuptools 79.0.1, wheel 0.48.0 and build 1.6.1. The cached
  setuptools wheel SHA-256 is
  `e147c0549f27767ba362f9da434eab9c5dc0045d5304feb602a0af001089fc51`;
  it matches the official PyPI artifact. No alternative build backend was used.
- Installed into a separate venv with local wheel dependencies, outside the
  source checkout. Python 3.12.14, Node 24.18.0, npm 11.16.0, Yarn 4.9.2.
- Verified wheel RECORD, all installed packaged bytes, 155 bundled Core files,
  both schemas and every packaged skill file against the pinned manifests.
  Checked actual Core module origins under the installed package.

The single final-wheel qualification passed **187 tests, zero failures, errors
or skips**:

| Group | Tests |
|---|---:|
| Real MCP stdio | 2 |
| Native npm/Classic/Berry audit fixtures | 18 |
| v3 workspace goal requests | 7 |
| Capabilities | 5 |
| Official migration evidence | 23 |
| Bundled Core partial domains, selection, metadata, semver, health and peers | 62 |
| Workspace inventory, boundaries and prepared retry | 42 |
| Goal/reference worker integration | 5 |
| Distribution resource boundaries | 9 |
| Skill structure and integrity | 14 |

Product imports came from the installed wheel; test fixtures came from the
checkout. Distribution/skill tests additionally inspect checkout build helpers
and canonical skill resources. This does not attest that a client's currently
loaded skill has changed. Real stdio children explicitly bind the same installed
site-packages directory. Windows localhost/asyncio tests needed execution outside
the restricted sandbox because socketpair initialization hangs inside it.

Reproduce after building and installing the candidate into a separate venv:

```powershell
# From a directory outside migration-mcp, with native npm/Yarn on PATH:
$env:PYTHONPATH = $null
$env:PYTHONHOME = $null
# Set TEMP/TMP to an existing writable test directory when required by the host.
<venv-python> -B <checkout>/scripts/qualify_migration_wheel.py `
  --wheel <candidate-wheel> --out <new-qualification.json>
```

The runner verifies that the installed manifest matches the checkout and that
installed bytes match the supplied wheel. It writes a test log beside the JSON.
Local final artifacts live in `../migration-checks/final-dist/` and
`../migration-checks/final-wheel-qualification.json` relative to this repository.

## Pro review disposition

[Architecture and implementation consultation](https://chatgpt.com/c/6af37ed4-eb48-83eb-afa4-5f5be2b6c1bd)
reviewed the supplied description and counterexamples. It was advisory review,
not independent execution or inspection of the whole local codebase.

| Review point | Disposition |
|---|---|
| Wheel qualification missing | Closed by the final installed artifact and the 187-test run |
| Cookie replay might reject its own changed request context | Code already returns the sequential branch before speculative context comparison; added real HTTP five-name cookie and no-store replay equality tests |
| K=2 can miss the only feasible companion | Confirmed limitation; exposed truncation/proof scope and EXPAND_SELECTED_DOMAIN; MCP K=2/K=3/v2 regression passes |
| Exact baseline already equals target | Added WORKSPACE_TARGET_ALREADY_SATISFIED before metadata/preflight; no fabricated migration count |
| Candidate/evidence identity across requests | Health evidence hash already participates in candidateId; details require process-local plan capability, exact request/candidate and snapshot hashes; planning IDs grant no execution authority |
| Four workers might be mistaken for a global host cap | Documented as a per-request window; host-wide concurrency remains a future load-control improvement |
| Batch output overflow | Capture is bounded and truncation becomes UNKNOWN; immediate overflow kill is not implemented, shared process deadline remains enforced |

## Remaining limits and next experiment

This v3 facade supports npm lock-v3 workspaces. It does not add universal
workspace execution or semantic migration analysis. A finite selected domain
can omit a feasible intermediate version; proof scope remains selected-domain
only. Long-running network calls can survive a local prefetch timeout until
their bounded request/worker lifetime ends. Global load limiting is not provided.

The catalog has three exact migration pairs. Suggested checks are recommendations
and local syntax observations are not semantic compatibility proofs. The
qualification uses synthetic fixtures, not a new broad real-project acceptance
corpus; historical project checkpoints retain their original artifact identities.

The next measurement is the [balanced agent comparison](MIGRATION_PILOT_PROTOCOL.md).
Its doctor and explicitly fake dry-run ran without model calls. Actual agent
performance is NOT_MEASURED. The intended product advantage is less repeated
agent discovery/search plus explicit, reusable evidence; a general speed or
competitive superiority claim requires that separate experiment.
