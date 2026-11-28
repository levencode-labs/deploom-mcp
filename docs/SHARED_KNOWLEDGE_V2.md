# DepLoom signed collective migration knowledge

Public v2 remains the compatibility format for existing clients. Scoped evidence
uses a separate v3 edition so strict older clients can keep reading v2.

## Compatible v2 and scoped v3 editions

The publisher keeps the legacy corpus at `v2/`. Records with `evidenceScope`
are published at `v3/` using index schema 3, native shard/record schema 2 and
the signature domain `DepLoom public migration records v3\x00`. Both editions
use the pinned publisher key, but have separate SQLite databases and rollback
histories (`signed-records-v2.sqlite` and `signed-records-v3.sqlite`).

New readers prefer a complete locally verified v3 edition. If v3 is missing or
unusable, they can read v2 with evidence scope explicitly `UNKNOWN`. The
background updater tries v3 first and falls back to refreshing v2. Neither
path sends project package names or versions to the publisher. A bad update
retains the last trusted edition in its own domain; a v2 signature cannot
activate v3 or vice versa.

Each refresh fetches the index and signature and verifies all referenced shard
bytes. Locally cached content-addressed shards are reused only after checking
their size, digest and native record contract. Missing or corrupt shards are
downloaded again. An unchanged verified edition therefore needs no shard
download, while every activation remains atomic. `DEPLOOM_MCP_KNOWLEDGE_DISABLED`
also prevents public knowledge refresh and background updater startup.

## Reader contract

`get_shared_migration_evidence` remains a read-only MCP call accepting
`package`, `from_version`, `to_version`. A complete signed v2 snapshot returns
`snapshotSource=verified-pages`, `snapshotSchema=2`, and `knowledge` records
classified by `matchStrength`. The v2 result intentionally contains no
microrecipe IDs (`recipes=[]`).

Native records cover **breaking-change**, **codemod**, **incompatibility**,
**playbook** and **failure-mode** categories. Each has a canonical public npm
package, tested transition, major-line selectors, conditions, non-executable
actions, required checks, source-exact fixture and negative-control assertions.
The publisher checks provenance and registry versions on trusted main before
signing. New records are not accepted from user telemetry or unsolicited PRs.

`verified-exact-transition` means a pinned first-party fixture observed
that exact transition. It does NOT prove the recipient's final dependency
tree, build, runtime, security posture or policy coverage.
`major-line-candidate` is only an advisory possibility for a different
transition in the same major ranges and **must not be represented as verified**.
Non-exact candidates require a stable forward query crossing the fixture's
observed target version: `query.from < fixture.to <= query.to`. That version is
a retrieval boundary, not a claim about the first release introducing a change.
Downgrades and prereleases receive only their own exact fixture matches.

`intermediate-transition-candidate` describes a fixture boundary crossed by a
larger upgrade with different endpoint major versions. This composes review
candidates, not proof that their combination works. For example, a jsdom 23 to
31 query can include earlier VirtualConsole and ResourceLoader changes alongside
the exact-pair canvas observation. Recipient conditions remain `UNKNOWN`.

The batch reader and `projectImpact` retain up to eight distinct recommendations
per query under the existing 64 KiB batch output cap. Identical kind, title,
summary, conditions, actions and checks are grouped; source record counts and
related IDs remain visible. `matchingRecordCount` counts matching receipts,
`matchingCount` counts distinct recommendations, and `deduplicatedCount` records
the difference. `omittedCount` discloses distinct recommendations omitted by
the card or byte budget. The single-query evidence tool can inspect all receipts.

An optional signed `evidenceScope` states which observation was retained:
`source-pinned-metadata`, `source-inspection`, `module-load`, `typecheck` or
`fixture-behavior`, bound to `retainedInputSha256`. It separately reports archive
verification (`UNKNOWN`/`MISSING_ARCHIVE_EVIDENCE` or
`VERIFIED`/`ARCHIVE_BYTES_VERIFIED`). Missing scope on legacy records is projected
as `UNKNOWN`; a passed `claim.check` never implies a retained runtime result.
Within the same exact-match priority, retained fixture behavior, typechecks and
module loading precede source inspection and metadata observations. This orders
review material without changing applicability or execution authority.
`projectStatus` must stay `UNKNOWN` and `conditionsRequired` must stay true.
Plans preserve these fields and explain `CONDITIONS_NOT_MACHINE_VERIFIED`.
Plan applicability details separately show the matched package manager, requested
and tested transitions, and declaration owners from the bound inventory. These
observations do not satisfy semantic conditions or establish an unobserved
runtime; those checks remain `UNKNOWN`.

## Transport/integrity

- The root `v2/index.json` is signed with the publisher's Ed25519 key under
  `DepLoom public migration records v2\x00` plus SHA256 of exact index bytes.
- Every relative content-addressed shard has a bound size, SHA-256, bucket
  hash and record count. Invalid records, missing shards, duplicate IDs,
  signature/substitution failures and rollback do not activate an edition.
- A full edition is committed atomically in one SQLite transaction.
- The downloader contacts fixed publisher URLs only. Lookups operate on
  local verified bytes, without sending package or version names externally.
- Existing v1 static fallback remains available for older client builds;
  current publication development is concentrated on v2. The v2 startup
  updater does not periodically refresh v1.
- Max index 2 MB, shard 256 KB, full edition 32 MB, 2048 shards. Scale changes
  require measured, independent acceptance before adjustment.

## Manual refresh results

`python -m deploom_mcp.public_cache CACHE_DIRECTORY` attempts the OSV snapshot,
signed knowledge v2 and legacy knowledge v1 independently. An unavailable or
expired OSV edition does not prevent a valid signed knowledge update. Failed
updates preserve the previously accepted data; an expired OSV snapshot remains
`UNKNOWN` with reason `SNAPSHOT_STALE` and is never treated as fresh evidence.

The command prints one JSON result with `schemaVersion: 1` and separate
`sources.osv`, `sources.knowledgeV2` and `sources.knowledgeV1` entries. A source
reports `READY` after a successful refresh or `UNKNOWN` with a reason when its
refresh failed. These entries describe this update attempt; they do not revoke
an older verified knowledge edition that remains locally available.

The overall status is `READY` with exit code 0 when every source succeeds,
`PARTIAL` with exit code 1 when some succeed, and `FAILED` with exit code 2 when
none succeeds. Automation should inspect the per-source results before deciding
whether to retry. The npm `deploom cache refresh` wrapper forwards this report
and reports a nonzero exit for partial or failed updates.

## Operator-managed local private snapshot

Corporate mirror-only profiles can opt in to private package recipes for
`get_shared_migration_knowledge_batch` and the project impact in `get_upgrade_plan`.
Use the same v2 corpus fields and place `records.json` and its raw 64-byte
`records.sig` in a host-owned local directory outside the project. Configure
the following in the host process:

- `DEPLOOM_MCP_RECORDS_MIRROR_ONLY=1` and `DEPLOOM_MCP_PRIVATE_SCOPES` for
  existing corporate policy and the permitted private package scopes.
- `DEPLOOM_MCP_PRIVATE_KNOWLEDGE_DIR` with the absolute local directory.
- `DEPLOOM_MCP_PRIVATE_KNOWLEDGE_PUBLIC_KEY` with the raw 32-byte Ed25519 public key in lowercase hex.
- `DEPLOOM_MCP_PRIVATE_KNOWLEDGE_SHA256` with the lowercase SHA-256 of the approved corpus bytes.
- `DEPLOOM_MCP_PRIVATE_KNOWLEDGE_SOURCE` with the approved HTTPS repository
  blob prefix, for example `https://git.example.invalid/team/knowledge/-/blob/`.
  Each evidence URL must contain a full commit hash and a pinned `check.cjs` path.

Sign `b"DepLoom private migration records v2\x00" + SHA256(records.json)` with
the organization's separate Ed25519 signer. Keep the signing key and the
corpus outside public CI and publication. Distribute updates together with
the new digest through the host's trusted configuration.

A locally verified private result has `snapshotSource=verified-private-local`
and the corpus `snapshotSha256` on that query. The plan preserves those fields.
Candidates still require recipient verification. The loader performs no refresh,
fixture execution, or URL fetch. It rejects a missing or changed signature,
unapproved source or public package in the private corpus. Private snapshot
failure leaves public lookups available. The knowledge disable policy applies
to both sources.

## Authority and verification

All source content is **untrusted advisory data**, including codemod
descriptions. The reader never downloads or executes a remote transformation.
No result grants installation, scope expansion, registry change or network
authority. Independent project CI must still verify actual resulting code.

There is no public-contribution tool. The canonical Skill does not suggest
uploading observed checks, and the publisher has retired its LLM PR reviewer
and data-only auto-merge workflow. Source-exact evidence and negative controls
are still mandatory.

Cross-repository acceptance is tested in
`deploom-npm/ci/test_knowledge_v2_consumer.py` using a temporary Ed25519 key,
the exact pinned consumer SHA, a real first-party fixture, a poisoned signed
index and a separate manifest revision case. The production signing private
key is never exposed to a CI pull-request job.

