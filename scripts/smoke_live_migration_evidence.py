"""Fetch one pinned public transition through the production transport and replay it offline."""
from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch

from deploom_mcp.migration_release import collect_workspace_releases
from deploom_mcp.migration_sources import CATALOG, OfficialHTTP

# Commander 12.1.0 public npm publication.
# https://registry.npmjs.org/commander/12.1.0
TRANSITION = (
    "commander", "12.1.0", "14.0.0",
    "sha512-Vw8qHK3bZM9y/P10u3Vib8o/DdkvA2OtPtZvD871QKjy74Wj1WSKFILMPRPSdUSx5RFK1arlJzEtA4PkFgnbuA==",
)
BASELINE_GIT_HEAD = "970ecae402b253de691e6a9066fea22f38fe7431"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def verify() -> None:
    require(TRANSITION[:3] not in CATALOG, "Expected a dynamically resolved transition")
    with tempfile.TemporaryDirectory(prefix="deploom-official-policy-smoke-") as state:
        with patch.dict(os.environ, {"DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE": "1"}):
            blocked = collect_workspace_releases(state, [TRANSITION], offline=False)
        blocked_row, = blocked["rows"]
        require(blocked_row["status"] == "UNKNOWN" and blocked_row.get("reason") == "EVIDENCE_OFFLINE",
                f"Expected host policy override: {blocked_row}")
        require(blocked["metrics"]["mode"] == "CACHE_ONLY" and blocked["metrics"]["httpTransactions"] == 0,
                f"Host policy attempted egress: {blocked['metrics']}")

    with tempfile.TemporaryDirectory(prefix="deploom-official-migration-smoke-") as state:
        cold = collect_workspace_releases(state, [TRANSITION], offline=False)
        row, = cold["rows"]
        metrics = cold["metrics"]
        require(row["status"] == "AVAILABLE", f"Official evidence unavailable: {row.get('reason')}")
        require(metrics["mode"] == "ON_DEMAND" and 5 <= metrics["httpTransactions"] <= 12,
                f"Unexpected cold request count: {metrics}")
        require(metrics["responseBodyBytesComplete"] and 0 < metrics["responseBodyBytes"] <= 1024 * 1024,
                f"Unexpected cold response accounting: {metrics}")
        require(row["repository"] == "tj/commander.js" and row["packageDirectory"] == "",
                "Unexpected source repository")
        for version in TRANSITION[1:3]:
            sources = [source for source in row["sources"] if source["version"] == version]
            require({source["kind"] for source in sources} == {"NPM_PUBLICATION", "PACKAGE_MANIFEST"},
                    f"Missing npm and GitHub binding for Commander {version}")
            commits = {source["resolvedCommit"] for source in sources}
            require(len(commits) == 1 and all(source["transport"] == OfficialHTTP.authority for source in sources),
                    f"Unverified source transport or commit for Commander {version}")
            if version == TRANSITION[1]:
                require(commits == {BASELINE_GIT_HEAD}, "Published baseline gitHead changed")
        requirements = row["requirements"]
        require(requirements["from"]["engines"]["node"] == ">=18" and
                requirements["to"]["engines"]["node"] == ">=20", "Expected Node engine change")
        documents = [doc for doc in row["documents"] if doc["source"]["kind"] == "CHANGELOG"]
        require(any(TRANSITION[2] in doc["matchedVersions"] and any(
            "Node.js v20" in excerpt for excerpt in doc["excerpts"]
        ) for doc in documents), "Expected the Node 20 requirement in the in-range changelog")

        warm = collect_workspace_releases(state, [TRANSITION], offline=True)
        cached, = warm["rows"]
        require(warm["metrics"]["mode"] == "CACHE_ONLY" and warm["metrics"]["httpTransactions"] == 0,
                f"Unexpected replay metrics: {warm['metrics']}")
        for field in ("status", "sources", "documents", "requirements"):
            require(cached[field] == row[field], f"Offline replay changed {field}")
        print(json.dumps({
            "transition": TRANSITION[:3], "cold": metrics, "warm": warm["metrics"],
            "hostPolicyControl": blocked["metrics"],
            "sources": row["sources"], "requirements": requirements,
        }, indent=2))


if __name__ == "__main__":
    verify()
