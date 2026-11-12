# Workspace goal requests

`get_upgrade_plan` accepts `project_path` and `workspace_request` only at the
outer level for this route. v3 handles supported npm lock-v3 workspaces.
The existing explicit v2 request remains unchanged.

```json
{
  "schemaVersion": 3,
  "goalKind": "TARGETED_UPGRADE",
  "registry": "https://registry.npmjs.org",
  "scope": [
    {"selector": {"name": "react", "owner": "packages/app"}, "approvedLines": [19], "exactTarget": "19.0.0"},
    {"selector": {"name": "react-dom", "owner": "packages/app"}, "approvedLines": [18, 19]}
  ],
  "candidateLimit": 8,
  "environment": {"node": "24.18.0", "npm": "11.16.0"}
}
```

The registry must already be authorized by host configuration and agree with
supported project configuration. A URL in a request grants no network authority.
Environment values describe planning assumptions, not verified runtime facts.
Missing engine evidence remains UNKNOWN. Omitting preflight performs planning;
existing opt-in preflight and host gates are unchanged. Discovery grants no
execution permission; installation, checks, runtime and acceptance remain NOT_RUN.

Each scope row selects exactly one non-peer declaration. Name alone is accepted
only when unique; owner and section narrow it. AMBIGUOUS_SCOPE returns up to 16
valid selectors and the full match count. Duplicate selectors are rejected.
Unselected declarations and their resolved versions are held. Inventory is
observed internally and checked again in the owning worker; drift is rejected.
Caller packuments and proposed topology are unavailable through v3. Use explicit
v2 when supplying a finite metadata snapshot or proposed modeled layout.

TARGETED_UPGRADE requires at least one exactTarget advancing a resolved baseline.
An already satisfied target returns WORKSPACE_TARGET_ALREADY_SATISFIED before
metadata or preflight; it is not a completed upgrade. Companions without an exact
target may remain at baseline because the objective minimizes changes before
version rank. MONOREPO_TO_POLICY requires the existing policy object; an empty
scope inspects the held baseline, not a completed migration.

Core selects at most K stable, non-downgrade versions per mutable declaration
inside approvedLines. K defaults to 8 and is bounded to 2..32. Descending latest
versions are selected, retaining an admitted baseline within K. Exact targets
are never truncated or replaced. Unknown evidence for a selected version is
reported separately and cannot be selected as a proven witness.

The response remains compact blueprint v2. intent.targets reports the concrete
selected versions, or null when selection could not be established.
intent.candidateSelection reports the versioned rule, limit, truncation and
proofScope. domain.selection=SERVER_BOUNDED and candidateSetDigest bind the
selected model. COMPLETE coverage and UNSAT refer only to that selected domain;
they say nothing about versions outside K. UNKNOWN alternatives prevent false
UNSAT. A static SAT is not proof of package-manager placement or runtime health.

One prepared evidence/model snapshot is reused for native attempts. A failed
complete assignment is excluded only within that request; npm error text never
becomes a new hard dependency constraint. Core candidate/model digests and the
MCP request/candidate identifiers support inspection of this distinction.

## Manifest edits and migration cards

Use `responseMode: "EDITS"` and `compactEdits: true` for a compact
workspace response. For an authorized exact-pin npm lock-v3 upgrade, also
set `editsStrategy: "NATIVE_INTENT"` and give the full owner, section,
name and version for each target. `operation: "ADD"` adds an explicitly
authorized exact companion in an existing dependency section in the same
patch as an upgrade. Leave the operation unset on ordinary upgrade rows.

`MANIFEST_INTENT_ONLY` gives a manifest patch bound to the inventory
digest and patch SHA-256. The client applies it with `deploom-workspace apply`
on a shared filesystem, then resolves the lock and verifies peers, held
dependencies and project checks using npm. A stale inventory rejects the patch.

Set `migrationCard: true` for transition-specific observations and acceptance
checks. For the ESLint 8.57.1 to 9.0.0 pilot, a host may opt into a donor
recipe by setting `DEPLOOM_MCP_MIGRATION_RECIPE_PATH` to the canonical absolute
path of a file outside the project and `DEPLOOM_MCP_MIGRATION_RECIPE_SHA256` to its exact
digest. A missing or mismatched recipe is omitted. Check each offered
recipe part against the recipient before use and verify the final result
on the recipient. The server does not collect or publish recipes automatically.
