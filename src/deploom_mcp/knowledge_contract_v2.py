"""Closed, non-executable public migration knowledge contract (v2-native).

Records are advisory *data*; this module neither runs transformations nor
promotes an agent's claims to verified installation/test outcomes.
"""
import json
import re

KINDS = frozenset({"breaking-change", "codemod", "incompatibility", "playbook", "failure-mode"})
PKG = re.compile(r"(?:@[a-z0-9][a-z0-9._~-]{0,79}/)?[a-z0-9][a-z0-9._~-]{0,99}\Z")
VERSION = re.compile(r"(?:0|[1-9][0-9]{0,7})(?:\.(?:0|[1-9][0-9]{0,7})){2}(?:-[0-9A-Za-z.-]{1,40})?\Z")
IDENT = re.compile(r"[a-z0-9][a-z0-9._-]{3,95}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")
SOURCE = re.compile(r"https://github\.com/levencode-labs/deploom-npm/blob/([a-f0-9]{40})/"
                    r"(fixtures/[a-z0-9_-]{1,64}/check\.cjs)\Z")
ALLOWED_CHECKS = frozenset({"install", "build", "test", "lint", "typecheck", "peer-compatibility"})
MANAGERS = frozenset({"npm", "yarn", "pnpm"})
MAX_RECORD_BYTES = 8192
MAX_CORPUS_BYTES = 4_000_000
MAX_RECORDS = 50_000
EVIDENCE_LEVELS = frozenset({"source-pinned-metadata", "source-inspection", "module-load",
                             "typecheck", "fixture-behavior"})


def _expect(value, explanation):
    if not value:
        raise ValueError("Invalid knowledge v2: " + explanation)


def _fields(data, expected):
    _expect(isinstance(data, dict) and set(data) == set(expected),
            "unexpected or missing fields")


def _string(value, limit=600):
    _expect(isinstance(value, str) and 1 <= len(value) <= limit
            and value == value.strip()
            and not any(ord(c) < 32 or ord(c) == 127 for c in value), "unsafe text")
    return value


def _list(value, *, maximum=8, item_limit=500):
    _expect(isinstance(value, list) and 1 <= len(value) <= maximum, "list cardinality")
    for entry in value:
        _string(entry, item_limit)
    _expect(len(set(value)) == len(value), "duplicate list elements")


def _package(value):
    _expect(isinstance(value, str) and PKG.fullmatch(value), "npm package name")


def _version(value):
    _expect(isinstance(value, str) and VERSION.fullmatch(value), "semver version")
    _expect("--" not in value and not value.endswith(("-", ".")), "semver prerelease")


def _major(version):
    return int(version.split(".", 1)[0])


def _validate_evidence_scope(scope):
    _fields(scope, {"schema", "level", "retainedInputSha256", "archiveStatus", "archiveReason",
                    "projectStatus", "conditionsRequired"})
    _expect(type(scope["schema"]) is int and scope["schema"] == 1, "evidence scope schema")
    _expect(isinstance(scope["level"], str) and scope["level"] in EVIDENCE_LEVELS, "evidence level")
    _expect(isinstance(scope["retainedInputSha256"], str) and SHA.fullmatch(scope["retainedInputSha256"]),
            "retained input digest")
    _expect((scope["archiveStatus"], scope["archiveReason"]) in (
        ("UNKNOWN", "MISSING_ARCHIVE_EVIDENCE"), ("VERIFIED", "ARCHIVE_BYTES_VERIFIED")),
        "archive evidence status")
    _expect(scope["projectStatus"] == "UNKNOWN" and scope["conditionsRequired"] is True,
            "fixture evidence cannot prove recipient applicability")


def validate_record(row, *, source_pattern=SOURCE):
    fields = {"schema", "id", "kind", "package", "fromMajor", "toMajor",
              "packageManagers", "verifiedTransitions", "title", "summary",
              "conditions", "actions", "verification", "negativeControls", "evidence"}
    if isinstance(row, dict) and "evidenceScope" in row:
        fields.add("evidenceScope")
        _validate_evidence_scope(row["evidenceScope"])
    _fields(row, fields)
    _expect(type(row["schema"]) is int and row["schema"] == 2, "schema")
    _expect(isinstance(row["id"], str) and IDENT.fullmatch(row["id"]), "id")
    _expect(isinstance(row["kind"], str) and row["kind"] in KINDS, "unknown knowledge kind")
    _package(row["package"])
    for key in ("fromMajor", "toMajor"):
        _expect(type(row[key]) is int and 0 <= row[key] < 100_000, key)
    managers = row["packageManagers"]
    _expect(isinstance(managers, list) and 1 <= len(managers) <= 3
            and all(isinstance(v, str) and v in MANAGERS for v in managers)
            and len(set(managers)) == len(managers),
            "package managers")
    transitions = row["verifiedTransitions"]
    _expect(isinstance(transitions, list) and len(transitions) == 1,
            "each verified transition requires its own pinned positive fixture claim")
    seen = set()
    for item in transitions:
        _fields(item, {"from", "to"})
        _version(item["from"])
        _version(item["to"])
        _expect(item["from"] != item["to"]
                and _major(item["from"]) == row["fromMajor"]
                and _major(item["to"]) == row["toMajor"], "transition major ranges")
        identity = (item["from"], item["to"])
        _expect(identity not in seen, "duplicate transition")
        seen.add(identity)
    _string(row["title"], 120)
    _string(row["summary"], 1200)
    _list(row["conditions"], maximum=12, item_limit=400)
    _list(row["actions"], maximum=12, item_limit=500)
    _list(row["verification"], maximum=12, item_limit=500)
    _expect(isinstance(row["negativeControls"], list) and 1 <= len(row["negativeControls"]) <= 8,
            "missing negative controls")
    for control in row["negativeControls"]:
        _fields(control, {"name", "expected", "fixtureAssertion"})
        _expect(isinstance(control["name"], str) and IDENT.fullmatch(control["name"]),
                "negative control name")
        _expect(control["expected"] == "reject", "negative controls must reject")
        _string(control["fixtureAssertion"], 100)
    e = row["evidence"]
    _fields(e, {"source", "fixtureSha256", "claim"})
    _expect(isinstance(e["source"], str) and source_pattern.fullmatch(e["source"]),
            "source must be first-party immutable fixture")
    _expect(isinstance(e["fixtureSha256"], str) and SHA.fullmatch(e["fixtureSha256"]),
            "fixture sha256")
    _fields(e["claim"], {"package", "from", "to", "check", "outcome"})
    c = e["claim"]
    _package(c["package"])
    _version(c["from"])
    _version(c["to"])
    _expect(c["package"] == row["package"] and (c["from"], c["to"]) in seen,
            "evidence claim must match an exact verified transition")
    _expect(isinstance(c["check"], str) and c["check"] in ALLOWED_CHECKS
            and c["outcome"] == "passed", "evidence outcome")
    if row["kind"] == "codemod":
        _expect(any("source" in x.lower() or "codemod" in x.lower()
                    for x in row["verification"]), "codemod requires source-code check")
    raw = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    _expect(len(raw) <= MAX_RECORD_BYTES, "record exceeds budget")
    return row


def validate_corpus(raw, *, source_pattern=SOURCE):
    _expect(isinstance(raw, bytes) and 0 < len(raw) <= MAX_CORPUS_BYTES,
            "corpus bytes")
    def unique(pairs):
        values = dict(pairs)
        _expect(len(values) == len(pairs), "duplicate JSON field")
        return values
    data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    _fields(data, {"schema", "records"})
    _expect(type(data["schema"]) is int and data["schema"] == 2, "corpus schema")
    _expect(isinstance(data["records"], list) and 1 <= len(data["records"]) <= MAX_RECORDS,
            "record count")
    seen_ids = set()
    seen_content = set()
    for record in data["records"]:
        validate_record(record, source_pattern=source_pattern)
        _expect(record["id"] not in seen_ids, "duplicate knowledge ID")
        seen_ids.add(record["id"])
        canonical = {key: value for key, value in record.items() if key != "id"}
        identity = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        _expect(identity not in seen_content, "duplicate knowledge content")
        seen_content.add(identity)
    return data


def _stable_version(version):
    if not isinstance(version, str) or not VERSION.fullmatch(version) or "-" in version:
        return None
    return tuple(int(part) for part in version.split("."))


def query_match(row, package, from_version, to_version, manager="npm"):
    if row["package"] != package or manager not in row["packageManagers"]:
        return None
    if {"from": from_version, "to": to_version} in row["verifiedTransitions"]:
        return "verified-exact-transition"
    before, after = _stable_version(from_version), _stable_version(to_version)
    tested = row["verifiedTransitions"][0]
    tested_before, tested_after = _stable_version(tested["from"]), _stable_version(tested["to"])
    # An observed fixture target is a retrieval boundary, not proof of the
    # version where a behavior was introduced or of applicability to this tree.
    # Never reuse upgrade guidance for a downgrade or an untested prerelease.
    if (None in (before, after, tested_before, tested_after)
            or not before < after or not tested_before < tested_after
            or not before < tested_after <= after):
        return None
    if before[0] == row["fromMajor"] and after[0] == row["toMajor"]:
        return "major-line-candidate"
    return "intermediate-transition-candidate"


def projection(row, strength):
    return {
        "id": row["id"], "kind": row["kind"], "package": row["package"],
        "title": row["title"], "summary": row["summary"],
        "conditions": row["conditions"], "actions": row["actions"],
        "verification": row["verification"],
        "negativeControls": row["negativeControls"], "evidence": row["evidence"],
        "testedTransition": dict(row["verifiedTransitions"][0]),
        "evidenceScope": dict(row.get("evidenceScope", {
            "schema": 1, "level": "UNKNOWN", "archiveStatus": "UNKNOWN",
            "archiveReason": "MISSING_ARCHIVE_EVIDENCE", "projectStatus": "UNKNOWN",
            "conditionsRequired": True,
        })),
        "matchStrength": strength, "authority": "ADVISORY_ONLY",
        "requiresProjectVerification": True,
    }

