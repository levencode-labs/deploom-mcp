"""Select a single approved registry for flat project planning."""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from semantic_version import NpmSpec

from deploom_mcp.dependency_audit import AuditError, checked_registry
from deploom_mcp.goal_contract import GoalContractError
from deploom_mcp.workspace_inventory import SECTIONS, _read, _read_bytes

_PUBLIC = {"https://registry.npmjs.org", "https://registry.yarnpkg.com"}
_REGISTRY_ROUTE = re.compile(r"^\s*[\"']?(?:registry|@[^\s=:]+:registry)[\"']?(?:\s|=|$)", re.I)


def _url(value: str | None) -> str | None:
    result = checked_registry(value)
    if result and any(c in result for c in "`$;{}"):
        raise AuditError("invalid-registry", "registry contains an unsupported character")
    return result


def is_corporate_registry_url(value: str) -> bool:
    try:
        parsed = urlsplit(_url(value))
    except (AuditError, ValueError):
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return (parsed.scheme == "https" and bool(host) and host.isascii()
            and not any(part in {".", ".."} for part in parsed.path.split("/"))
            and not any(host == domain or host.endswith("." + domain)
                        for domain in ("npmjs.org", "npmjs.com", "yarnpkg.com")))


def private_metadata_registry(selected: str | None) -> str | None:
    approved = os.environ.get("DEPLOOM_MCP_ALLOWED_PROJECT_REGISTRY", "").rstrip("/")
    if (selected and selected.rstrip("/") == approved and is_corporate_registry_url(approved)
            and os.environ.get("DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE") == "1"):
        return approved
    return None


def _guard_private_declarations(project: Path, selected: str | None) -> None:
    scopes = {item.strip() for item in os.environ.get("DEPLOOM_MCP_PRIVATE_SCOPES", "").split(",") if item.strip()}
    if not scopes or private_metadata_registry(selected):
        return
    try:
        _, manifest = _read(project / "package.json", 2 * 1024 * 1024, root=project)
    except FileNotFoundError:
        return
    except (GoalContractError, OSError, ValueError):
        raise AuditError("project-manifest-unavailable", "Could not safely inspect private dependency scopes") from None
    for section in SECTIONS:
        declarations = manifest.get(section)
        if not isinstance(declarations, dict):
            continue
        for name, spec in declarations.items():
            if name.split("/", 1)[0] not in scopes or not isinstance(spec, str) or not spec.strip():
                continue
            # An ordinary semver selector or dist-tag triggers a packument read.
            # Core also guards the actual fetch, including newly discovered peer names.
            try:
                NpmSpec(spec.strip())
            except ValueError:
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]*", spec.strip()):
                    continue
            raise AuditError("private-metadata-registry-required",
                             "Private dependency metadata requires restricted mode and the host-approved corporate HTTPS registry")


def _configured_routes(raw: bytes, *, yarn: bool) -> tuple[set[str], bool]:
    routes = {}
    has_endpoint_auth = False
    for line in raw.decode("utf-8-sig").splitlines():
        if line.lstrip().startswith("//"):
            has_endpoint_auth = True
        if not _REGISTRY_ROUTE.match(line):
            continue
        match = (re.fullmatch(r"\s*([^\s=]+)\s+(.+?)\s*", line) if yarn else
                 re.fullmatch(r"\s*([^\s=]+)\s*=\s*(.+?)\s*", line))
        if not match:
            raise AuditError("project-registry-unsupported", "Use a literal project registry configuration")
        key, value = match.groups()
        key = key.strip("\"'").lower()
        if value[:1] in {"\"", "'"} and value[-1:] == value[:1]:
            value = value[1:-1]
        if key in routes:
            raise AuditError("project-registry-ambiguous", "Remove the repeated project registry route")
        routes[key] = _url(value)
    if (routes or has_endpoint_auth) and "registry" not in routes:
        raise AuditError("project-registry-ambiguous", "Single-registry planning requires a project default route for scope or endpoint authentication settings")
    return set(routes.values()), any(key.startswith("@") for key in routes)


def _yarn_yaml_mapping(node: yaml.Node) -> dict[str, yaml.Node]:
    if not isinstance(node, yaml.MappingNode):
        raise AuditError("project-registry-unsupported", "Expected a mapping in Yarn registry configuration")
    fields = {}
    for key, value in node.value:
        if not isinstance(key, yaml.ScalarNode) or key.tag != "tag:yaml.org,2002:str":
            raise AuditError("project-registry-unsupported", "Expected string keys in Yarn registry configuration")
        if key.value in fields:
            raise AuditError("project-registry-ambiguous", "Yarn registry configuration has a repeated key")
        fields[key.value] = value
    return fields


def _configured_yarn_yaml_routes(raw: bytes) -> tuple[set[str], bool]:
    try:
        if any(isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken)) for token in yaml.scan(raw)):
            raise AuditError("project-registry-unsupported", "Yarn registry configuration cannot use YAML aliases")
        document = yaml.compose(raw, Loader=yaml.SafeLoader)
    except (yaml.YAMLError, RecursionError):
        raise AuditError("project-registry-unsupported", "Could not parse Yarn registry configuration") from None
    if document is None:
        return set(), False

    config = _yarn_yaml_mapping(document)
    default = config.get("npmRegistryServer")
    scopes = _yarn_yaml_mapping(config["npmScopes"]) if "npmScopes" in config else {}
    scoped = [settings["npmRegistryServer"] for scope in scopes.values()
              if "npmRegistryServer" in (settings := _yarn_yaml_mapping(scope))]
    if scoped and default is None:
        raise AuditError("project-registry-ambiguous", "Single-registry planning requires a Yarn default route")

    routes = set()
    for node in ([default] if default is not None else []) + scoped:
        if not isinstance(node, yaml.ScalarNode) or node.tag != "tag:yaml.org,2002:str":
            raise AuditError("invalid-registry", "Yarn registry must be a literal HTTP(S) URL")
        routes.add(_url(node.value))
    return routes, bool(scoped)


def planning_registry(project: Path, explicit: str | None) -> str | None:
    approved = _url(os.environ.get("DEPLOOM_MCP_ALLOWED_PROJECT_REGISTRY"))
    restricted = approved is not None and os.environ.get("DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE") == "1"
    selected = _url(explicit.strip() if isinstance(explicit, str) else explicit)
    if selected is not None and restricted and selected != approved:
        raise AuditError("project-registry-not-authorized", "Planning registry must match the MCP host's approved registry")
    if not project.is_dir():
        return selected
    routes = set()
    scoped_routes = False
    try:
        for name in (".npmrc", ".yarnrc", ".yarnrc.yml"):
            path = project / name
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            raw = _read_bytes(path, 16 * 1024, root=project)
            if name == ".yarnrc.yml":
                configured, scoped = _configured_yarn_yaml_routes(raw)
            else:
                configured, scoped = _configured_routes(raw, yarn=name == ".yarnrc")
            routes.update(configured)
            scoped_routes = scoped_routes or scoped
    except (GoalContractError, OSError, UnicodeError):
        raise AuditError("project-registry-unavailable", "Could not safely read the project registry configuration") from None
    # Core's flat planner sends all registry reads to one endpoint. An explicit
    # default cannot authorize routing scoped or transitive packages elsewhere.
    if len(routes) > 1:
        raise AuditError("project-registry-ambiguous", "Single-registry planning does not support multiple project registry routes")
    if selected is not None:
        if routes and selected not in routes and (restricted or scoped_routes):
            raise AuditError("project-registry-conflict", "Explicit registry differs from the configured project route")
    elif routes:
        selected = next(iter(routes))
        if not selected.startswith("https://"):
            raise AuditError("project-registry-unsupported", "Automatic project registry selection requires HTTPS")
        if selected != approved and (restricted or selected not in _PUBLIC):
            raise AuditError("project-registry-not-authorized",
                             "Pass the registry explicitly or configure the MCP host's approved project registry")
    elif restricted:
        selected = approved
    _guard_private_declarations(project, selected)
    return selected
