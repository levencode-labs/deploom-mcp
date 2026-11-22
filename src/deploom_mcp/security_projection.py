"""Project exact, current-version security evidence without publishing raw provider messages."""

OSV_REASONS = frozenset({
    "PUBLIC_PACKAGE_UNCONFIRMED", "PRIVATE_SCOPE_EXCLUDED", "UNSUPPORTED_OSV_RANGE",
    "MISSING_SEVERITY", "SNAPSHOT_READ_ERROR", "SNAPSHOT_STALE", "SNAPSHOT_UNAVAILABLE",
    "INVALID_EXACT_VERSION", "LOCKED_PUBLIC_ARTIFACT_UNCONFIRMED",
})
ROW_REASONS = OSV_REASONS | {"NON_REGISTRY_SPEC", "REGISTRY_METADATA_UNAVAILABLE", "CURRENT_VERSION_UNRESOLVED", "PRIVATE_METADATA_REGISTRY_UNAPPROVED"}
REGISTRY_REASONS = frozenset({
    "REGISTRY_NOT_APPROVED", "INVALID_EXACT_PAIR", "REGISTRY_HTTP_ERROR", "RESPONSE_TOO_LARGE",
    "REGISTRY_TRANSPORT_ERROR", "INVALID_ADVISORY_RESPONSE", "COVERAGE_UNCONFIRMED",
    "MATCH_BUDGET_EXHAUSTED", "INVALID_ADVISORY_OR_RANGE", "REGISTRY_EXACT_VERSION", "REGISTRY_NO_REPORTED_ADVISORIES",
})


def project_current_security(value):
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    if status == "ASSESSED":
        source, count = value.get("source"), value.get("findingCount")
        if source not in ("OFFLINE_OSV_NPM", "REGISTRY_BULK_ADVISORY", "OFFLINE_OSV_NPM_AND_REGISTRY") or type(count) is not int or count < 0:
            return None
        evidence = {"status": status, "source": source, "findingCount": count}
        if (source == "OFFLINE_OSV_NPM" and count == 0
                and value.get("publicIdentitySource") == "NPM_PUBLIC_PACKUMENT"):
            evidence["publicIdentitySource"] = "NPM_PUBLIC_PACKUMENT"
    elif status == "UNKNOWN" and isinstance(value.get("reason"), str) and value["reason"] in ROW_REASONS:
        evidence = {"status": status, "reason": value["reason"]}
    else:
        return None
    for key, codes in (("osvReason", OSV_REASONS), ("registryReason", REGISTRY_REASONS)):
        code = value.get(key)
        if isinstance(code, str) and code in codes:
            evidence[key] = code
    return evidence


def current_security_coverage(dependencies):
    sources = {}
    reasons = {}
    unreported = 0
    for row in dependencies:
        result = (row.get("evidence") or {}).get("currentSecurityEvidence")
        if result is None:
            unreported += 1
        elif result["status"] == "ASSESSED":
            source = result["source"]
            sources[source] = sources.get(source, 0) + 1
        else:
            reason = result["reason"]
            reasons[reason] = reasons.get(reason, 0) + 1
    if unreported == len(dependencies):
        return None
    return {"totalRows": len(dependencies), "unreportedRows": unreported,
            "assessedBySource": sources, "unknownByReason": reasons}
