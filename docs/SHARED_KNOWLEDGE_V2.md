# Shared migration evidence v2 consumer (development)

This adds the **signed, content-addressed** v2 reader without changing the
public MCP tool's parameter shape. \`get_shared_migration_evidence\` returns
\`snapshotSource=verified-pages\` when it has a trusted v2 edition, plus
\`snapshotSchema=2\`. It continues to return the existing v1/bundled results
when no complete trusted v2 edition is installed.

Startup refresh fetches only **fixed publisher URLs**, not package- or
project-specific URLs. Every index is verified under the bundled Ed25519 key
with the distinct v2 domain. The index pins each shard's path, size, SHA-256
and row count. Each shard is checked against the publisher's closed schema,
including its SHA-256-derived package bucket.

All shards must pass before a single SQLite transaction activates the new
edition. Rollback and equal-revision substitution are rejected; failed
updates leave the last known-good edition intact. The v2 lookup is strictly
local and performs **no network access**, preventing disclosure of a
project's dependency inventory through per-package requests.

Initial limits: index 2 MB, individual shard 256 KB, edition 32 MB, at most
2,048 shards. These limits are deliberate and require measured changes before
introducing a larger public corpus. The producer currently admits only
existing maintainer-reviewed v1 records, so this is a format/protocol
migration, **not** a claim that previously unknown migrations are verified.

The planner must continue to treat returned recipes as ADVISORY_ONLY, not
as proof of correctness or permission to edit, install, or execute code.
