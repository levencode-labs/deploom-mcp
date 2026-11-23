"""Host policy for network evidence, separate from cached knowledge eligibility."""

import os


def external_network_evidence_disabled() -> bool:
    return (os.environ.get("DEPLOOM_MCP_LOCAL_CACHE_ONLY") == "1"
            or os.environ.get("DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE") == "1")


def cached_knowledge_disabled() -> bool:
    return (os.environ.get("DEPLOOM_MCP_DISABLE_EXTERNAL_EVIDENCE") == "1"
            or os.environ.get("DEPLOOM_MCP_KNOWLEDGE_DISABLED") == "1")
