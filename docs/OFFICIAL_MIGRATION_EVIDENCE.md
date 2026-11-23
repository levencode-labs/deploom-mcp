# Optional official migration references

`get_goal_plan` accepts an optional `migration_evidence_request`. It appends an
`officialMigrationEvidence` section to the existing proposal. Omit the parameter
for the original planning route: no knowledge retrieval, cache reads or parser
loads are performed. Exact planning still does not invoke Z3. The tool advertises
an open-world hint because an explicitly requested reference section may access
public official sources.

```json
{
  "schema": "official_migration_evidence.v1",
  "mode": "ON_DEMAND",
  "transitions": [{"package": "strip-json-comments", "from": "3.1.1", "to": "5.0.0"}],
  "detail": "summary",
  "localApplicability": "OFF"
}
```

The transition must match the observed root version and the proposal's selected
target. This does not authorize that transition, source repair or execution.
The explicit providers cover `strip-json-comments` 3.1.1 to 5.0.0,
Vite 6.4.5 to 7.0.0 and ESLint 8.57.1 to 9.0.0. Vite includes plugin-react
4.5.2 as a **reference**, not an automatically selected companion. ESLint
includes the tagged official migration guide and before/after package metadata. Other exact transitions return UNSUPPORTED;
rename/alias mappings are not guessed. No-transition performs no HTTP requests.
Prerelease targets require a separate policy and are not admitted here.

## Authority and conflicts

`reference.suggestedChecks` links recommendations to source IDs. Every check
remains RECOMMENDATION_ONLY, NOT_RUN and localApplicability=UNKNOWN. Examples
include configuration linting/conditional RuleTester, Vite build/runtime and
JSON comment handling. `codemodApplicability=NOT_ESTABLISHED`; none of these
references runs a command or establishes a local acceptance profile.

References are REFERENCE_ONLY; local syntax observations are PLANNING_ONLY.
`verificationAuthority` and local `executionAuthority` remain NONE. No section
can change the original goal, policy, binding, assignment, check profile,
executionReady or checkpoint. A source document, hash or cache entry is not an
execution receipt. The keyword helper is reused only as LABELLED_KEYWORD_HINTS;
its legacy labels do not become established breaking behavior or solver rules.

Package metadata, source literals and documentation claims are distinguished.
For example, strip-json-comments 5.0.0 has conflicting trailingCommas defaults
in its types and source/README. Both claims and their exact source references
are retained; this mechanism does not execute the source to resolve the conflict.
A guide's applicability or a compatible peer range does not establish complete
npm placement, a successful install or working application behavior.

## Retrieval and cache

Only catalog-verified public GitHub repository/tag/artifact mappings are used.
Tags are resolved to immutable commits, annotated tags are bounded, and each
UTF-8 body receives SHA-256. HTTPS connects to a validated public DNS address
with certificate/hostname validation. Arbitrary URLs, credentials, cookies,
private addresses and redirects are rejected; no environment proxy credentials
are forwarded. This direct public HTTPS transport does not modify proxy settings.
Unavailable transport remains UNKNOWN, without automatic retries or fallback to
main/master. Project paths, source, lockfiles and local observations are never
included in outbound requests.

Limits shared by the whole evidence request: 12 HTTP transactions, 128 KiB per
body, 1 MiB total body bytes, 30 seconds and the remaining overall tool deadline.
The implementation is sequential (within the maximum concurrency of two).
Unknown stream lengths mark byte accounting incomplete and stop further fetches.
Metrics count HTTP body bytes actually read, not headers, TCP overhead or tokens.

The host-owned `migration-evidence` cache under DEPLOOM_MCP_STATE_DIR uses atomic
records and content hashes. A pin is revalidated after 24 hours. Drift, corrupt
records/bodies and backward/future cache clocks fail closed. The local operator
controls the cache; hashes are not signature attestation against a hostile writer.
The operator state root and inspected project must be disjoint; overlapping
paths are rejected before cache access or temporary input writes. Source
retrievedAt records the observed version-pin time used for TTL, not a network
transfer timestamp for every immutable artifact.
`DEPLOOM_MCP_MIGRATION_OFFLINE=1` permits only fresh, verified cached inputs.
Stale/missing offline inputs remain UNKNOWN; no silent stale data is served.
`detail: full` is a bounded source view with explicit textTruncated flags;
canonical commit URLs and full content hashes remain available.

## Local syntax inspection

`localApplicability: ON` optionally uses an already installed **host** TypeScript
6.0.3 library. Configure DEPLOOM_MCP_TYPESCRIPT_PATH to its absolute typescript.js
path and DEPLOOM_MCP_TYPESCRIPT_SHA256 to the exact file hash. The library must be
outside the inspected project. There is no automatic installation or loading of
project-owned parsers, imports, plugins or configuration. A missing/mismatched
host parser gives UNKNOWN rather than an absence claim.

The pinned helper parses JS/TS text without executing it. It records literal
imports, re-exports, require calls, dynamic imports with static specifiers and
member accesses tied to local import bindings. The helper also recognizes
selected literal configuration keys. Runtime resolution, effective settings and
semantic API compatibility remain unknown. Unresolved dynamic loads, aliases,
callback, spread and computed configuration, parse failures, unsupported
Vue/Svelte/Astro files and truncated coverage produce `UNKNOWN`. Scoped absence
applies only to the inspected syntax.

Coverage includes eligible/scanned/unreadable/unsupported counts and limits
(256 files, 128 KiB per file, 1 MiB total source text). Findings are bound to the
observed source identity and file digest inventory. Changes during inspection
mark them STALE. Source text is passed only to the local pinned parser in an
owned temporary input file, removed on normal completion; no text is sent to
reference providers. Caller verification remains mandatory.

## Validation boundaries

No-agent tests separately exercise retrieval/pins/cache, syntax observation and
normal MCP attachment. A strong comparator may use the same official docs,
hashes, cache, TypeScript checking and official codemods. Known fixtures are not
an independent blind holdout. Warm-cache measurements do not prove an advantage
over another equally cached client, and no agent token, repair success or ROI
claim follows from a smaller response. Skill behavior requires its own loaded
client validation. Existing closed project trials are not repeated by this feature.

## Migration cards in workspace EDITS

In a v3 `TARGETED_UPGRADE` request, set `responseMode: "EDITS"` and
`migrationCard: true`. Both modeled EDITS and `NATIVE_INTENT` attach
`migrationCard.projectImpact` to the same response. The card uses the
baseline lock version and the target selected for each declaration. Equal
transitions share one card across owners. Different installed baselines in
the same monorepo retain separate cards.

One bounded local scan covers all selected package names. It suggests
specific checks for observed React DOM, TypeScript, Vite, Vitest and
CommonJS module-format patterns. For React DOM legacy render, hydration or
unmount syntax, the card links to the official codemod and gives verification
instructions. Other packages can still produce import and file observations.
Common config filenames are reported even when the optional host parser is
unavailable. Root-owned and workspace-owned imports are distinguished.
References outside the selected declaration owners are possible consumers
whose installed versions and post-upgrade resolution need investigation.
Existing ESLint and strip-json-comments cards retain their fields.

Static findings guide review. They carry no verification or execution
authority. The project impact scan makes no network requests. The existing
manifest patch, resolver and project checks still govern acceptance.

### Exact migration knowledge in EDITS

The default `migrationEvidenceMode: "CACHE_ONLY"` reads the local signed
Knowledge Cache v2 once for the selected transitions and checks the local
official-source cache for eligible misses. The response reports signed
fixture evidence separately from official references. Knowledge Cache v2
retains its existing signature and scope checks. The Skill uses the lookup
already present in the card and batches only exact transitions absent from it.

Explicit `migrationEvidenceMode: "ON_DEMAND"` allows bounded retrieval for
an authorized public npmjs route. The source version and integrity come from
the lockfile. The npm registry must report the exact packages and versions,
including matching baseline tarball integrity. The package repository and
subdirectory must agree at both ends, with an exact published `gitHead` and
matching `package.json` at each commit. Repository metadata serves as a
nomination. The transport constructs its own allowlisted URLs and validates
publication and source identity. This evidence does not independently attest
that either tarball was built from that Git commit.

DepLoom includes only excerpts and source digests from relevant version
sections, authenticated package metadata and bounded target guides or
release notes. A release tag must resolve to the published commit. Guide
and repository-release scope stay visible. Mentions of observed symbols
are review hints. Check the provenance and recipient applicability before
acting on a reference, and review a codemod's conditions before using it.

The official path handles at most two transitions per plan and shares
the existing 12-request, 128 KiB-per-body and 1 MiB budgets with a 9-second
worker watchdog. Offline or corporate restrictions force local reads.
It keeps the original TLS and redirect restrictions. Packages without
matching public artifacts or source manifests, private scopes, relocated
packages and unsupported repository layouts produce explicit gaps. Already
obtained sources are reusable offline within their freshness window.

Renovate also separates the npm repository URL and package directory,
looks up release or changelog data, and filters by version. This path
uses exact published commits and the existing DepLoom evidence transport
to bind the smaller set of sources it returns.
[Renovate changelog discovery](https://docs.renovatebot.com/key-concepts/changelogs/)
