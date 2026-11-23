# DepLoom signed collective migration knowledge v2

**Status: development-only draft; no live Pages publication or new wheel release.**

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
