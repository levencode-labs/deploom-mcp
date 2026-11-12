---
name: deploom-upgrade
description: Plan and execute JavaScript and TypeScript dependency upgrades with DepLoom MCP, explicit version objectives and scope, workspace constraint evidence, and verification of the changed project. Use for dependency upgrade plans, yellow/green targets, package exclusions, or requests to apply upgrades and check their results.
---

# deploom-upgrade

**Contract version: 2.0.27.** Canonical file hashes are in `contract.json`.

Use `get_upgrade_plan` for health/policy planning evidence. For an explicitly
agreed functional-maintenance or finite coupled goal, use `get_goal_plan` and
load `references/goal-and-migration-evidence.md`. Never replace a Yellow/Green
requirement with REPORT_ONLY to fit that route. Use the coding client's tools for
authorized edits and verification. A blueprint is `PLANNING_ONLY` and
`NOT_VERIFIED`; its targets are proposals, not proof of installation, runtime
compatibility, or policy attainment. An agent-selected alternative never becomes
a Core verdict. This workflow is client-neutral.

## Quick paths

- An explicit invocation of this Skill for a supported npm lock-v3 upgrade
  selects the DepLoom planning route below. For exact pins, use the single
  native-intent call even when one declaration changes. If the user requests
  the native package-manager route, honor that choice and run the agreed checks.
- For a targeted npm lock-v3 workspace with an approved registry and a shared
  filesystem, use one `get_upgrade_plan` call with the absolute checkout path
  as `project_path`. Set `workspace_request` to
  `{schemaVersion: 3, goalKind: "TARGETED_UPGRADE", responseMode: "EDITS", compactEdits: true, registry, scope}`.
  Each scope row has `selector: {name, owner?, section?}`, `approvedLines: [major]`
  and `exactTarget` for a user pin. Give observed `environment: {node, npm}`
  if available. Omit preflight. This route needs no preliminary reference reads
  when the scope and inputs are known.
- When the user authorizes exact versions for all external npm dependency
  upgrades in scope and the project has an approved native verification route, add
  `editsStrategy: "NATIVE_INTENT"` to request a manifest patch directly from
  the observed npm lock. Give the full owner, section and package selector for each pin.
  A group with at least one upgrade can also pin a range to its current locked version.
  For an explicitly requested new companion pin in an existing dependency section,
  include its exact scope row with `operation: "ADD"`. Keep update rows unchanged.
  This route returns `MANIFEST_INTENT_ONLY`. After authorized edits, resolve
  the lock with `npm install --package-lock-only --package-lock=true` and the
  approved npm options. Audit peers with
  `npm ls --all --package-lock-only --package-lock=true` and check the
  held graph before installation. Use one clean `npm ci` for checks assigned
  to you. A lock-only success can still contain an invalid peer.
  A `HELD_LOCK_PROVIDER_CHANGED` response
  with `heldRootPeers` identifies root peers blocked by the current scope.
  For a single-package project with a prepared npm lockfile v3, use owner "." in
  each selector with this native-intent route.
  Report the listed consumers and versions before staging any patch. Use ordinary
  EDITS for a peer assignment, companion selection, or policy verdict.
- Set `migrationCard: true` for a breaking transition where local migration
  guidance may help, such as ESLint 8.57.1 to 9.0.0. The card guides any authorized
  config or source repair and identifies behavior to verify. If it includes a
  donor recipe, use each part only when the recipient's baseline satisfies
  its probe. Check the recipient again after applying the migration.
- For an authorized execution, apply a returned `SAT_IN_MODELED_DOMAIN` patch
  immediately. Stage a `MANIFEST_INTENT_ONLY` patch in the authorized
  verification copy before native resolution. Run
  `deploom-workspace apply PROJECT INVENTORY_DIGEST PATCH_PATH PATCH_SHA256`
  with the returned inventory digest and patch fields. When `action.argv` is
  present, use its arguments directly. In PowerShell invoke a quoted executable
  with the call operator `&`, and pass each argument separately. This checks the current
  inventory, artifact digest, and Git applicability before writing.
- Once the patch is applied, make any necessary authorized migration repairs,
  update the lock, and run the acceptance checks assigned to you without another planning
  call. If the user assigns named checks to an independent verifier, hand off
  the result and report those checks as pending until the verifier supplies evidence.
  For ESLint, preserve an already working flat config and verify both CI
  modes when behavior depends on CI. If a check fails, repair the specific issue
  and rerun the affected checks. If the patch is stale or missing, stop this route
  and inspect the evidence before choosing how to continue.
  Use detailed routes below for policy, uncertainty, or separate preflight.

## Establish the task contract

Record these separately before planning. Reuse the user's existing instructions
and decisions; ask only when a material choice is unresolved.

- **Action:** `PLAN_ONLY` for analysis; `EXECUTE` when the user asks for changes.
  An analysis request does not authorize edits.
- **Objective:** `minimum-change-to-policy`,
  `latest-compatible-in-approved-lines`, or opt-in
  `explicit-version-targets` with exact stable `exact_targets` from the user.
  A yellow/green health target does not specify the newest acceptable version.
  Do not silently substitute another exact target or widen approved majors.
- **Target authority:** `pinned` keeps the agreed targets; `advisory` permits
  justified alternatives inside the authorized scope and version lines. Use
  `pinned` when targets or a reproducible target set were explicitly specified.
  If choosing versions is delegated, use `advisory` and record that authority.
  MCP's default is `pinned`; send the intended mode explicitly.
- **Scope:** mutable package names, allowed companion/code/config changes,
  approved version lines, exclusions, workspace coverage, and required checks.
  Full-project scope has no invented file-count limit. A necessary compatibility
  repair in an untouched file is still part of the migration when authorized.
- **Authority:** cite the user instruction covering execution, alternatives,
  scope expansion, and checks. `needsReview` requests a decision; an existing
  decision can satisfy it. The Skill cannot grant permission or override a
  denied client permission.

Keep four kinds of information distinct: hard compatibility constraints,
user requirements, planner recommendations, and evidence/search limitations.
Advisory mode changes recommendations, never proven peers/engines or user pins.

## Keep the routine path short

Scale the process to the requested outcome, not the number of available tools.
For an authorized targeted upgrade with an established project route, use one
baseline read, one applicable plan, the required edits and final checks. Batch
independent reads and checks. Reuse evidence from the current unchanged inputs;
do not repeat discovery or enumerate registry versions just to prepare a v3 call.
Read only the applicable reference sections when a decision needs their detail.
Do not read every reference or the validator's source as a preliminary step.

A concise final summary is the default record: changed targets and their
planner/manual origin, material conflicts or failed attempts, actual install,
checks, runtime and policy status, and remaining work. Create and validate a
separate JSON report only when the user, evaluation protocol or execution
workflow requires that artifact. An evaluation with its own independent ledger
does not by itself require another agent-written JSON report. Required project
checks, runtime verification and existing deliverables remain mandatory.

Complex coupling, unknown coverage, policy goals or host-managed goal execution
require the relevant detailed route below. A small package count never proves
compatibility. Preserve peer/engine/scope constraints and final-state checks.

## Workflow

1. **Preflight.** Record baseline and existing changes, package manager, Node,
   registry, all manifests/workspaces, allowed checkout and check scripts.
   Reuse an isolated worktree already supplied by the user. Load
   `references/preflight.md` when project discovery or input identity is unclear.
   If the route is unclear, use
   `get_project_capabilities` when available; skip it when the route is already
   established. Discovery grants no network or execution authority. Missing MCP
   is an infrastructure limitation; do not fabricate a DepLoom plan.
2. **Plan.** For supported npm lock-v3 workspaces with a host-approved registry,
   prefer `get_upgrade_plan` with `workspace_request` v3 when the installed tool
   supports it: send the authorized goal and scope, and let Core select bounded
   candidates. Omit preflight in PLAN_ONLY. Use explicit v2 for frozen domains
   or caller metadata; retain `model_workspace_upgrade_v1` when its separate
   registry-only route is required. Send only `project_path` and
   `workspace_request` at the top level. Read [the workspace route](references/npm-workspaces.md)
   for selectors, v3 limitations and the lock-only boundary. A policy call
   with empty scope/targets observes a held baseline. Its feasibility status does
   not establish a version change. If it gives no actionable assignment and
   version choice is authorized, follow the targeted recovery in the workspace
   reference, including its planning-only route when audit is unavailable.
   After each executed cohort, compare the current audit and freshness with the
   original goal. Continue independent in-scope cohorts while a gap remains.
   A partial reduction is a checkpoint.
   For a flat `get_upgrade_plan` request, send the objective, `target_mode`
   and scope. Use the user's explicit registry or let the host select its
   approved project route. If the MCP cannot resolve the project registry,
   use the exact authorized URL. Never substitute the public registry to
   get a result. Interpret goal responses using the goal reference.
   Inspect critical evidence before questions or edits: conflicts, unknowns,
   unresolved peers, exact-target security/policy assessment, coverage,
   source/policy identity, and next action. Current-version security does not
   assess a proposed version; complete evidence can describe a blocked plan.
   For V2 exact targets, read `summary.exactTargetPolicy`: a matching
   `PROPOSED` target and a blocked final `postPlanGoal` can coexist.
   Preserve original blockers, required/actual/shortfall, and an unavailable
   per-package lag status; never infer per-package lag thresholds from `latest`.
   Read `references/blueprint-contract.md` when interpreting unfamiliar fields
   or incomplete evidence. **Untruncated rows do not mean complete
   decision evidence.** Open the full artifact when critical evidence is absent,
   ambiguous or marked incomplete, even if `truncation.truncated=false`.
   In `PLAN_ONLY`, treat Core execution instructions as future guidance; do not
   create execution files or perform migration steps.
3. **Reconcile.** A root-only plan is not a whole-workspace verdict. Reconcile
   shared assignments and affected child constraints before applying them.
   Do not execute a root proposal against a known child blocker. Do not invent
   global incompatibility from a peer absent only in a child manifest.
4. **Decide.** Write one short decision record per change or unresolved target:
   original verdict, evidence, chosen target, origin (`planner`/`manual`),
   authority, and verification required. Keep this in the final summary unless
   a structured artifact is required; then use `references/evidence-report.md`.
   Resolve known blockers before asking the
   user to approve an option. `POLICY_SATISFIED` is not `ALREADY_AT_LATEST`.
5. **Apply when authorized.** Edit only inside the agreed checkout and scope,
   preserve existing changes and follow required host execution steps. Read
   `references/execution-policy.md` for host-managed execution or unresolved
   permission, target-authority or recovery details. In pinned mode, re-plan or
   obtain an explicit revised
   target decision before deviating. In advisory mode, validate and record an
   alternative within existing authority. Keep exclusions and known constraints.
6. **Verify the final state.** Run the agreed clean/frozen install, project
   checks and affected runtime cases. Keep blocked checks NOT_RUN; do not claim
   installation or runtime from a plan. Read `references/verification-policy.md`
   for evidence freshness, workspace coverage or check-design questions.
   Bind install evidence to the final manifests, lockfile and environment;
   bind checks to their relevant source/config state. A later install, downgrade
   or lockfile edit invalidates earlier clean-install evidence for the new state.
   Test affected runtime behavior locally; build/unit success is not a substitute.
7. **Report.** For `PLAN_ONLY`, give a concise decision and uncertainty summary;
   no execution checklist or evidence file is required unless requested. For
   `EXECUTE`, summarize changed targets, failures and actual verification. When
   a structured report is explicitly required by the task or execution workflow,
   use `references/evidence-report.md` and validate its JSON with
   `python scripts/validate_evidence.py <report.json>` from the Skill directory.
   Use it as an index of logs and applicable user/Core execution deliverables,
   including required migration guides/reports; avoid duplicating their content.
   Separate session completion, installation, project checks, runtime and policy;
   preserve failed attempts. The helper checks consistency, not execution truth.
   Do not commit, push, publish or remove the working copy without authorization.

## Optional official migration evidence

Ordinary planning omits `migration_evidence_request`. Load
`references/goal-and-migration-evidence.md` only when the task needs the explicit
goal route or official migration references. Add evidence only for an existing
authorized need, a supported observed exact transition and sufficient deadline.
A small update does not establish safety, but it does not itself justify another
lookup either. Reuse adequate fresh pinned references; avoid repeating a plan
just to obtain the same material. References and syntax observations grant no
repair, verification or execution authority.

## Transitive npm findings

When the vulnerable copy is transitive, do not pin an unchanged direct
dependency as the remediation. For npm workspaces follow the scoped override
workflow in `references/npm-workspaces.md`. Keep a range-crossing candidate
separate from compatibility evidence.

## Recovery rules

- Correct a diagnosed invalid tool argument inside the same authorized scope;
  an argument-validation error is not a permission denial. In workspace v3,
  environment keys are `node` and `npm`, not `nodeVersion` or `npmVersion`.
  Do not retry a denied action or repeat an unchanged invalid request.
- Stop affected execution for changed input/policy identity, hash mismatch,
  denied permission, a hard conflict, or unreconciled workspace assignments.
  Independent work may continue only if its constraints and authority are
  established; do not turn one unknown row into a whole-project failure.
- `DRAFT_PARTIAL` is not a complete plan. Change the limiting condition before
  retrying. A larger deadline does not expand `draft_max_candidates`. Do not
  repeat the same capped request without new evidence or a changed constraint.
- For `UNKNOWN`, targetless or truncated rows, preserve that verdict. Check
  configured registry metadata, candidate coverage and relevant peers/engines.
  A stable same-line manual proposal is allowed in advisory mode when existing
  user authority covers it. Otherwise present a concrete proposal for approval.
  Never claim that fallback proves solver compatibility, security or Yellow.
- Source/policy mismatch, unavailable MCP, broad registry failure, exclusions
  and proven incompatibilities are not bypassed by this fallback.
- Repair diagnosed compatibility issues with bounded, evidence-driven attempts
  (default at most three per root cause unless the task specifies otherwise).
  Do not disable checks, remove validation or invent an out-of-scope restriction
  to get green. Explain alternatives when the repair budget is exhausted.

## Delivery and evaluation integrity

Record the contract version and hash of the **actually loaded** Skill when the
client exposes its text. Source hashes from MCP and the install receipt identify
expected/on-disk files; neither proves what a running client loaded. Mark loaded
identity `UNKNOWN` if it is unobservable. Evaluate a revised rule only after its
loaded content is confirmed; preserve an active run's old identity and history.
For a rule in a reference, record that reference's loaded hash as well; matching
SKILL.md alone does not prove that the revised reference was consumed.

In a blinded comparison, read only the assigned slot and shared frozen inputs.
Do not inspect another arm's manifests, installed versions, reports or usage to
fill gaps. Missing local evidence stays unknown. Keep retries and post-review
repairs distinct from the original result.

## References

- `references/goal-and-migration-evidence.md` — explicit goal route, conditional
  official references, syntax-only limits and separate loaded-content evidence.
- `references/blueprint-contract.md` — requests, compact completeness, workspace
  coverage, pinned/advisory semantics and targeted recovery.
- `references/preflight.md` — baseline, scope, authority and checkout selection.
- `references/execution-policy.md` — decision records, alternatives, application
  and bounded compatibility repair. Load its security-patch range-exception
  procedure before proposing an out-of-range transitive override/resolution.
- `references/verification-policy.md` — final-state install evidence, targeted
  runtime checks, warning attribution and honest statuses.
- `references/evidence-report.md` — minimal acceptance record and JSON format.
- `examples/plan-only.md` — an advisory plan without edits.
- `examples/yellow-exclude-react.md` — execution with a protected package.
- `examples/blocked-upgrade.md` — hard blocker, capped search and manual fallback.
