"""Host entry point for security evidence on an exact, previously resolved Yarn Berry candidate."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re

from deploom_mcp.audit_worker import read_bytes
from deploom_mcp.dependency_audit import audit_dependencies, checked_registry


async def audit_yarn_candidate(
    project, receipt, expected_identity, registry, output_dir, deadline, use_system_proxy, node, yarn_cli,
):
    document = json.loads(read_bytes(receipt))
    identity = document["identity"]
    canonical = (json.dumps(identity, sort_keys=True, separators=(",", ":")) + "\n").encode()
    observed = hashlib.sha256(canonical).hexdigest()
    registry = checked_registry(registry)
    if (expected_identity != observed or document["identity_sha256"] != observed
            or identity["schema"] != 1 or identity["authority"] != "HOST_YARN_RESOLVER"
            or registry is None or identity["registry"] != registry):
        raise ValueError("YARN_CANDIDATE_RECEIPT_MISMATCH")
    files = identity["candidate_inputs"]
    yarn_version = identity["yarn_version"]
    node_sha, yarn_sha = identity["node_sha256"], identity["yarn_cli_sha256"]
    if (not isinstance(files, dict) or not {"package.json", "yarn.lock"}.issubset(files)
            or not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                       for value in (*files.values(), node_sha, yarn_sha))
            or not isinstance(yarn_version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", yarn_version)
            or int(yarn_version.split(".")[0]) < 4 or not node.is_absolute() or not yarn_cli.is_absolute()):
        raise ValueError("INVALID_YARN_CANDIDATE_RECEIPT")
    return await audit_dependencies(
        project_path=str(project), package_manager="yarn-berry", registry=registry,
        output_dir=str(output_dir), deadline_seconds=deadline, use_system_proxy=use_system_proxy,
        _candidate_binding={"files": files, "lockSha256": files["yarn.lock"], "yarnVersion": yarn_version,
                            "nodePath": str(node), "nodeSha256": node_sha,
                            "yarnCliPath": str(yarn_cli), "yarnCliSha256": yarn_sha,
                            "registrySha256": hashlib.sha256(registry.encode()).hexdigest(),
                            "resolverIdentitySha256": expected_identity},
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--expected-identity-sha256", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--yarn-cli", type=Path, required=True)
    parser.add_argument("--deadline-seconds", type=int, default=60)
    parser.add_argument("--use-system-proxy", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(audit_yarn_candidate(
            args.project, args.receipt, args.expected_identity_sha256, args.registry, args.output_dir,
            args.deadline_seconds, args.use_system_proxy, args.node, args.yarn_cli,
        ))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}))
        return 1
    print(json.dumps(report))
    return 0 if report.get("candidateAudit", {}).get("status") == "BOUND_SECURITY_AUDIT_COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
