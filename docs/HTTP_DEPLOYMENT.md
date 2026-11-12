# Streamable HTTP deployment

[User guide](../USERGUIDE.md) · [Sharing with colleagues](SHARING_WITH_COLLEAGUES.md)

Deploom MCP uses **stdio by default**. The separate Streamable HTTP entrypoint is appropriate for controlled deployment, not an unauthenticated public server.

## Local loopback example

```sh
python -m deploom_mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

Use an MCP Streamable HTTP client with endpoint `http://127.0.0.1:8000/mcp`.

DNS-rebinding protection applies even on non-loopback binds. By default, only loopback Host and Origin headers are accepted. For a gateway with a different Host header, supply `--allowed-host` for the exact host/port. For a browser Origin, supply `--allowed-origin` for the exact allowed origin. Custom lists replace the loopback defaults; do not use wildcards. Verify gateway Host forwarding/rewrite explicitly.

## Container example

From a clean source checkout, initialize the exact pinned Core first:

```sh
git submodule update --init --recursive
docker build -t deploom-mcp:0.2.18 .
docker run --rm -p 127.0.0.1:8000:8000 \
  --mount type=bind,src=/absolute/host/projects,dst=/projects,readonly \
  deploom-mcp:0.2.18
```

`project_path` must be a **server/container path** such as `/projects/example-app`. A read-only bind mount supports planning; any explicitly authorized execution involving writes requires a separately reviewed deployment and permissions. The container's unprivileged user must be able to read mounted paths. State, output and caches live server-side and require deliberate persistence where necessary.

## Pilot sizing and state

For an initial planning-only pilot with one concurrent request and one or two
public checkouts, start with a Linux host with 2 vCPU, 4 GiB RAM and 30 GiB SSD.
Measure peak memory, storage growth and request latency before increasing
concurrency. Allow outbound access to the approved registry and advisory
services used by the selected route. Use a writable persistent volume for
`DEPLOOM_MCP_STATE_DIR` when state must survive restarts. Keep writable worktrees
and native install/test jobs in a separately scoped runner.

## Security boundary

**There is no built-in authentication or per-user project-path isolation.** The HTTP process exposes both planning and separately authorized execution surfaces. Connected clients share the server's OS identity, mounts, and state.

Never mount credentials or unrelated confidential repositories. For multiple users, use an authenticated TLS gateway with access controls and network restrictions; run separate instances for mutually untrusted users with least-privilege mounts. Do **not** publish the container port directly. Inspect host execution authorizations before any write-enabled setup.

Set client response/idle timeouts to accommodate chosen tool deadlines. Apply resource limits, concurrency controls and process monitoring in your deployment, and separately validate registry/proxy access from **inside** the server/container. This is a deployment starting point, not certification for any specific infrastructure.
