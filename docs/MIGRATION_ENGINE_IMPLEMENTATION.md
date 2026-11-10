# Migration engine implementation

Work branches: `codex/migration-engine` in Core and MCP. Base: Core
824eef4 (public d1e01d9 plus supported root npmrc binding); MCP 60aef86
(public cea8ea2 plus standalone audit routing, npmrc binding and coverage guards).
Existing candidate work is preserved and requires release qualification.

## Decisions

Architecture consultation (2026-11-11):
https://chatgpt.com/c/6af37ed4-eb48-83eb-afa4-5f5be2b6c1bd
Advice is independently checked against code and tests; it is not runtime evidence.

- Add one discovery tool: get_project_capabilities, no network or execution.
- Extend get_upgrade_plan with a versioned goal/scope request; preserve explicit
  workspace v2 and standalone routes. Server binds inventory and selects finite
  candidates inside approved majors. No implicit authority expansion.
- Keep existing isolated execution tools. Defer a universal trial tool until
  backend receipt and authorization contracts are equivalent.
- Extend migration evidence coverage where supported; do not add a general
  impact tool claiming semantic coverage for syntax-only observations.
- Preserve all variables, versions and objective ranks for partial evidence.
  Unknown candidate literals are excluded only from the proven submodel.
  SAT requires a complete witness; partial-domain exhaustion is UNKNOWN.
  Original shared-slot/topology checks must still run against full domains.
- Prepare one evidence/model snapshot per request and reuse it for native retry.
  ERESOLVE only excludes the exact tried vector in that request; structural
  parser output remains a hint unless independently proven.
- Apply prefix pruning, bounded metadata prefetch and batch npm-semver as
  independent measured changes. Preserve route/credential/deadline boundaries.

## Delivery checklist

- [x] Isolated Core/MCP worktrees; upstream candidate fixes preserved.
- [x] Workspace discovery prefix pruning; 42 inventory/boundary tests pass.
- [x] Overlapping/nested/literal/star prefix and selected-path budget regressions.
- [x] Noisy-tree measurement: 3610 to 4 scandir calls; same inventory digest.
  A 2100-noise-directory tree no longer exhausts the irrelevant traversal budget.
- [x] Initial Core partial-domain implementation; 10 new regressions pass.
- [x] Partial-domain evidence propagated through compact/details schemas.
- [x] Installed final wheel: partial-domain contract through real MCP stdio.
- [x] get_project_capabilities: five read-only/privacy/authority regressions and real stdio pass.
- [x] Goal-driven v3 scope binding and bounded selection; explicit v2 preserved.
  Ambiguity, drift, unknown exact target, already-satisfied target and K=2/K=3
  selected-domain exhaustion are covered.
- [x] Prepared evidence/model reuse across retries; stable model/evidence digests.
  Core tests and MCP two-attempt ERESOLVE regression prove no refetch.
- [x] Bounded metadata prefetch: isolated workers, private/stateful fallback,
  deadline and real HTTP cookie/no-store replay checks.
- [x] Batched npm-semver: strict batch/ID protocol, bounds, shared deadline
  and differential checks against the native scalar engine.
- [x] Source and installed native audit suite: 18 tests pass with npm 11.16.0
  and Yarn 4.9.2; npm, Classic and Berry fixtures/adapters covered.
- [x] Scoped lag rejection before egress and same-origin redirect denial.
- [x] Exact ESLint references, source-linked NOT_RUN checks and route docs.
- [x] Packaged skill 2.0.15 uses v3 when supported; capability discovery only
  when the route is unclear. Skill content/packaging validation passes.
- [x] Pro reviewed implementation description and counterexamples; findings
  independently checked. Pro did not inspect or execute the local code.
- [x] Core 36586b7 pinned, final wheel built/installed offline; 187 checks pass
  with zero errors, failures or skips. See MIGRATION_ENGINE_RESULTS.md.
- [x] Paired comparison protocol documented; no-model doctor and fake dry-run.
- [ ] Actual balanced agent A/B measurement: NOT_MEASURED, separate follow-up.
  See MIGRATION_PILOT_PROTOCOL.md; component timings are not agent superiority.

## Acceptance boundaries

Planning, native resolution, frozen install, checks/runtime, audit and delivery
remain separate. Exact targets and held declarations/lock versions cannot be
silently relaxed. Unknown audit coverage cannot become PASS. Evidence is bound
to input/config/registry/runtime identity; no corporate project data is published.

A/B uses equal model/reasoning, prompt, tools except MCP, budgets and independent
oracle; isolated checkouts/cache copies, balanced order, cold/warm reported
separately. Count all assigned feasible runs, including B runs not using MCP.
Report accepted-within-budget rate and paired time-to-independent-PASS, failures
and unknowns. A microbenchmark or one migration pair is not a general win.

## Local validation notes

Inventory test startup inside the Windows sandbox hung in Python asyncio
socketpair/accept before entering async code. The same 39 tests passed in 6.7s
outside that sandbox with workspace-local TEMP/TMP. This is a test-environment
constraint, not an inventory regression.

## Discovery boundary

Inventory only opens directories that can still match an admitted workspace
pattern. A terminal member is not traversed below unless another pattern needs
that subtree. Irrelevant links are not followed or statted. Selected paths and
ancestors retain symlink/reparse rejection and bound-handle checks. Directory
entry budgets still count scanned entries; relevant large trees fail explicitly.
Full lock owners/links remain independently reconciled and discovery/input reads
repeat for drift detection. Any later operation copying a broader tree must
perform its own path checks; inventory does not authorize that broader tree.
