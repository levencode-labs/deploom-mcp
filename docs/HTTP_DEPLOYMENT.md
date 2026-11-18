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
docker build -t deploom-mcp:0.2.19 .
docker run --rm -p 127.0.0.1:8000:8000 \
  --mount type=bind,src=/absolute/host/projects,dst=/projects,readonly \
  deploom-mcp:0.2.19
```

`project_path` must be a **server/container path** such as `/projects/example-app`. A read-only bind mount supports planning; any explicitly authorized execution involving writes requires a separately reviewed deployment and permissions. The container's unprivileged user must be able to read mounted paths. State, output and caches live server-side and require deliberate persistence where necessary.

## Restricted Archestra pilot

Use a candidate build that supports `--pilot-project`. Give one trusted tester
group one standalone sample checkout and one dedicated state volume. The
option exposes `get_project_capabilities` and the flat form of
`get_upgrade_plan`, and binds both to the configured server-side path. Workspace
requests and caller overrides of the registry, proxy and output path are
rejected at the MCP boundary. The cache TTL is bounded to 3600 seconds.

Set the following container arguments and environment in the Archestra
self-hosted server settings. Keep the image ENTRYPOINT and append the arguments,
or use the equivalent full command below. Set port `8000` and path `/mcp`.
Use the exact `Host` header forwarded by Archestra.

    python -m deploom_mcp --transport streamable-http --host 0.0.0.0 --port 8000 \
      --allowed-host deploom-mcp.example.invalid:8000 --pilot-project /projects/sample

    DEPLOOM_MCP_STATE_DIR=/data/deploom-mcp
    DEPLOOM_MCP_ALLOWED_PROJECT_REGISTRY=https://npm-mirror.example.invalid
    DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE=1
    DEPLOOM_MCP_USE_SYSTEM_PROXY=false

Mount `/projects/sample` read-only with a `package.json` and supported lockfile,
both regular files. For a Kubernetes ConfigMap, use individual file subPath
mounts or copy the files in an init container. A direct ConfigMap directory
mount presents symlinks that project discovery rejects. Mount a dedicated
writable PVC at `/data/deploom-mcp`. Restrict pod ingress to Archestra and
egress to DNS and the approved registry. Keep one replica for this trial and
authorize every tool call through the authenticated gateway. Limit parallel
calls and disk usage. Use a separate instance, mounts and state for a different
trust group.

In the Inspector, list both tools and call `get_project_capabilities` with
`{"project_path":"/projects/sample"}`. Call `get_upgrade_plan` with the same
arguments and confirm `PLANNING_ONLY`. Check the dependency rows and evidence
limits. Confirm that a different path, a `workspace_request`, an output override,
an alternate registry and a direct call to `verify_goal_candidate` are rejected.
Confirm gateway access with an allowed identity and rejection with an
unassigned identity. Proxy and advisory evidence depend on the deployment;
unavailable evidence appears as `UNKNOWN`.

### Cache acceptance

Successful anonymous planning metadata is reused under
`DEPLOOM_MCP_STATE_DIR/registry-metadata-cache`. The cache is separated by
registry, request context, client build and redirect policy. A warm repeat
within TTL can avoid packument GETs. An expired entry can be conditionally
revalidated when the registry provides ETag or Last-Modified. Authenticated,
cookie-bearing and proxied requests bypass the shared metadata cache; private,
no-store and no-cache responses are also excluded. Native installs, audit and
verification use their own evidence paths.

To validate persistence, run a first plan against an approved anonymous
mirror, repeat it in a second session and restart the pod with the same PVC.
Compare the returned dependency decisions and the mirror's metadata GETs.
Use `registry_metadata_ttl=0` for an uncached control. Keep mirror telemetry
separate from advisory traffic. Start with a fresh cache on the first rollout
of this build. The state volume also holds generated artifacts, so use a
dedicated quota and review its growth during the pilot.

On a local checkout with MCP dependencies installed, run the deterministic
loopback comparison (no external registry required).

    python benchmarks/measure_http_cache.py --source /path/to/v0.2.19 --output baseline.json
    python benchmarks/measure_http_cache.py --output candidate.json --expect-reuse

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
