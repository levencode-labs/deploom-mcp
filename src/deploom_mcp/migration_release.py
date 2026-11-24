"""Official release references bound to exact public npm publications."""
from __future__ import annotations

import base64
import binascii
import json
import re
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

from semantic_version import Version

from deploom_mcp.knowledge_contract_v2 import PKG
from deploom_mcp.host_evidence_policy import external_network_evidence_disabled, mirror_knowledge_only
from deploom_mcp.migration_sources import EvidenceUnavailable, SHA, TTL, SourceStore, encoded
from deploom_mcp.process_runner import run_process

PUBLIC_NPM = "https://registry.npmjs.org"
_GITHUB_REPO = re.compile(
    r"(?:git\+https://github\.com/|https://github\.com/|git://github\.com/|"
    r"git\+ssh://git@github\.com/|git@github\.com:|github:)"
    r"([a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}/[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99})/?\Z"
)
_DIRECTORY = re.compile(r"[a-zA-Z0-9_@.-]+(?:/[a-zA-Z0-9_@.-]+)*\Z")
_HEADING = re.compile(r"^(#{1,6})[ \t]+([^\n]+)")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_SETEXT_TITLE = re.compile(r"^ {0,3}(?![ \t#>]|(?:[-+*]|\d+[.)])(?:[ \t]|$))\S")
_MISSING_DOCUMENT = {"OFFICIAL_HTTP_404", "EVIDENCE_OFFLINE"}
_VERSION_TITLE = re.compile(r"\[?v?(\d+\.\d+\.\d+)(?=\s|$|\])")
_SIGNAL = re.compile(
    r"\bbreak(?:ing|s)?\b|\bmigrat\w+|\bremov\w+|\bdeprecat\w+|\brequir\w+|"
    r"\bdefaults?\b|\brenam\w+|\bcodemods?\b|\bnode\.?js\b|\bcompatib\w+",
    re.IGNORECASE,
)
_REPLACEMENT_HEADING = re.compile(
    r"^#{1,6}[ \t]+.*`[^`]{1,80}`[ \t]+instead of[ \t]+`[^`]{1,80}`",
    re.IGNORECASE,
)
_GUIDES = {
    ("eslint", 9): "docs/src/use/migrate-to-9.0.0.md",
    ("vite", 7): "docs/guide/migration.md",
    ("vite", 8): "docs/guide/migration.md",
    ("vitest", 4): "docs/guide/migration.md",
}


def public_npm_integrity(package, version, record):
    """Identify a public, non-aliased npm lock artifact without following its URL."""
    if (not isinstance(package, str) or not PKG.fullmatch(package)
            or not isinstance(version, str) or len(version) > 32
            or not isinstance(record, dict) or record.get("link") or record.get("name", package) != package):
        return None
    try:
        parsed = Version(version)
        if parsed.prerelease or parsed.build:
            return None
    except ValueError:
        return None
    filename = f"{package.rsplit('/', 1)[-1]}-{version}.tgz"
    if record.get("resolved") != f"{PUBLIC_NPM}/{package}/-/{filename}":
        return None
    value = record.get("integrity")
    try:
        if (isinstance(value, str) and len(value) == 95 and value.startswith("sha512-")
                and len(base64.b64decode(value[7:], validate=True)) == 64):
            return value
    except (ValueError, binascii.Error):
        pass
    return None


def _unique_pairs(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate field")
    return result


def _object(document):
    try:
        data = json.loads(document["text"], object_pairs_hook=_unique_pairs)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise EvidenceUnavailable("PUBLISHED_METADATA_INVALID")
    return data


def _published(store, package, version, before_integrity=None):
    url = f"{PUBLIC_NPM}/{quote(package, safe='')}/{version}"
    document = store._text(url, ["npm-exact", package, version], max_age=TTL)
    data = _object(document)
    dist = data.get("dist")
    integrity = public_npm_integrity(package, version, {
        "resolved": dist.get("tarball"), "integrity": dist.get("integrity")
    }) if isinstance(dist, dict) else None
    if (data.get("name") != package or data.get("version") != version
            or not integrity or before_integrity is not None and before_integrity != integrity):
        raise EvidenceUnavailable("PUBLIC_NPM_ARTIFACT_MISMATCH")

    repository = data.get("repository")
    url_hint = repository.get("url") if isinstance(repository, dict) else repository
    directory = repository.get("directory", "") if isinstance(repository, dict) else ""
    match = _GITHUB_REPO.fullmatch(url_hint) if isinstance(url_hint, str) and len(url_hint) <= 256 else None
    if (not match or not isinstance(directory, str) or len(directory) > 180
            or directory and (not _DIRECTORY.fullmatch(directory)
                              or any(part in {".", ".."} for part in directory.split("/")))):
        raise EvidenceUnavailable("PUBLISHED_REPOSITORY_UNVERIFIED")
    commit = data.get("gitHead")
    if not isinstance(commit, str) or not SHA.fullmatch(commit):
        raise EvidenceUnavailable("PUBLISHED_COMMIT_UNAVAILABLE")
    return data, document, (match.group(1).removesuffix(".git").lower(), directory, commit)


def _raw(store, repo, commit, artifact):
    url = f"https://raw.githubusercontent.com/{repo}/{commit}/{quote(artifact, safe='/')}"
    return store._text(url, [repo, commit, artifact])


def _source(doc, kind, version, commit=None):
    return {
        "kind": kind, "version": version, "url": doc["url"], "sha256": doc["sha256"],
        "transport": doc["transport"], "resolvedCommit": commit,
        **({"observedAt": doc["observedAt"]} if "observedAt" in doc else {}),
    }


def _markdown_lines(text):
    fence = None
    offset = 0
    for raw in text.splitlines(keepends=True):
        line = raw.rstrip("\r\n")
        marker = re.match(r"^ {0,3}(\x60{3,}|~{3,})(.*)$", line)
        if marker:
            symbols, trailing = marker.groups()
            if fence is None:
                fence = symbols
            elif symbols[0] == fence[0] and len(symbols) >= len(fence) and not trailing.strip():
                fence = None
        elif fence is None:
            yield offset, line
        offset += len(raw)


def _heading_spans(text):
    previous = None
    for offset, line in _markdown_lines(text):
        if heading := _HEADING.match(line):
            yield offset, offset + heading.end(), len(heading[1]), heading[2]
        elif previous is not None and (underline := _SETEXT.fullmatch(line)):
            start, title = previous
            if (text[start + len(title):offset] in {"\n", "\r\n", "\r"}
                    and _SETEXT_TITLE.match(title)):
                yield start, offset + len(line), 1 if underline[1][0] == "=" else 2, title.strip()
        previous = offset, line


def _notes(text):
    lines = []
    for _, raw in _markdown_lines(text):
        line = raw.strip()
        if not (_SIGNAL.search(line) or _REPLACEMENT_HEADING.match(line)):
            continue
        item = " ".join(line.lstrip("#*- ").split())[:240]
        if item and item not in lines:
            lines.append(item)
        if len(lines) == 6:
            break
    return lines


def _ranges(text, before, after):
    headings = list(_heading_spans(text))
    versions, excerpts = [], []
    for index, (_, start, level, title) in enumerate(headings):
        matched = _VERSION_TITLE.match(title)
        if matched is None:
            continue
        try:
            version = Version(matched[1])
        except ValueError:
            continue
        if before < version <= after:
            end = next((headings[i][0] for i in range(index + 1, len(headings))
                        if headings[i][2] <= level), len(text))
            versions.append(str(version))
            excerpts.append(text[start:end])
    return versions[:20], _notes("\n".join(excerpts))


def _requirements(data):
    fields = {}
    for kind in ("engines", "peerDependencies"):
        value = data.get(kind)
        if isinstance(value, dict):
            fields[kind] = {key: constraint for key, constraint in list(value.items())[:8]
                            if len(key) <= 180 and isinstance(constraint, str) and len(constraint) <= 180}
    if data.get("type") in ("commonjs", "module"):
        fields["type"] = data["type"]
    if "exports" in data:
        exports = data["exports"]
        subpaths = sorted(key for key in exports if key == "." or key.startswith("./")) if isinstance(exports, dict) else []
        selected = [key for key in subpaths if len(key) <= 180][:12]
        fields["exports"] = {"subpaths": selected, "omitted": len(subpaths) - len(selected)}
    return fields


def collect_public_transition(store, package, before, after, baseline_integrity):
    """Resolve GitHub docs from published package identity, never from a caller URL."""
    result = {"authority": "REFERENCE_ONLY", "status": "UNKNOWN", "sources": [],
              "coverage": "PARTIAL", "rangeComplete": False}
    if not baseline_integrity:
        return {**result, "reason": "PUBLIC_BASELINE_UNPROVEN"}
    try:
        old, old_doc, old_identity = _published(store, package, before, baseline_integrity)
        new, new_doc, new_identity = _published(store, package, after)
        if old_identity[:2] != new_identity[:2]:
            raise EvidenceUnavailable("PUBLISHED_REPOSITORY_CHANGED")
        repo, directory, new_commit = new_identity
        old_commit = old_identity[2]
        base = f"{directory}/" if directory else ""
        for version, commit in ((before, old_commit), (after, new_commit)):
            manifest = _raw(store, repo, commit, base + "package.json")
            data = _object(manifest)
            if data.get("name") != package or data.get("version") != version:
                raise EvidenceUnavailable("PUBLISHED_SOURCE_MANIFEST_MISMATCH")
            result["sources"].append(_source(manifest, "PACKAGE_MANIFEST", version, commit))
        result["sources"].extend((
            _source(old_doc, "NPM_PUBLICATION", before, old_commit),
            _source(new_doc, "NPM_PUBLICATION", after, new_commit),
        ))
        result.update(status="AVAILABLE", requirements={"from": _requirements(old), "to": _requirements(new)},
                      publicationIdentity="NPM_METADATA_AND_MATCHING_GIT_MANIFEST",
                      tarballSourceAttested=False, repository=repo, packageDirectory=directory, documents=[])
    except (EvidenceUnavailable, OSError, ValueError) as exc:
        return {**result, "status": "UNKNOWN", "sources": [],
                "reason": str(exc) if isinstance(exc, EvidenceUnavailable) else "PUBLISHED_EVIDENCE_UNAVAILABLE"}

    lower, upper = Version(before), Version(after)
    guide = _GUIDES.get((package, upper.major)) if lower.major < upper.major else None
    changelog = (base + "CHANGELOG.md", "CHANGELOG")
    candidates = ([(guide, "MIGRATION_GUIDE"), changelog] if guide else
                  [changelog, (base + "MIGRATION.md", "MIGRATION_GUIDE")])
    gaps = []

    def add_document(artifact, kind):
        try:
            document = _raw(store, repo, new_commit, artifact)
        except EvidenceUnavailable as exc:
            gaps.append({"kind": kind, "reason": str(exc)})
            return False
        versions, snippets = _ranges(document["text"], lower, upper) if kind == "CHANGELOG" else ([], _notes(document["text"]))
        source = _source(document, kind, after, new_commit)
        result["documents"].append({
            "source": source, "matchedVersions": versions, "excerpts": snippets,
            "scope": "VERSION_SECTIONS" if kind == "CHANGELOG" else "TARGET_COMMIT_GUIDE",
        })
        return True

    for artifact, kind in candidates:
        add_document(artifact, kind)
    if any(gap["kind"] == "CHANGELOG" and gap["reason"] in _MISSING_DOCUMENT for gap in gaps):
        for name in ("History.md", "HISTORY.md"):
            if add_document(base + name, "CHANGELOG") or gaps[-1]["reason"] not in _MISSING_DOCUMENT:
                break
    if not any(doc["scope"] != "VERSION_SECTIONS" or doc["matchedVersions"] for doc in result["documents"]):
        # Independent monorepo package tags are preferred. A candidate tag must
        # resolve to the published commit before a release body is accepted.
        tags = (f"{package}@{after}", f"v{after}") if directory else (f"v{after}",)
        for tag in tags:
            try:
                if store._pin(repo, tag)["commit"] != new_commit:
                    raise EvidenceUnavailable("RELEASE_TAG_PUBLICATION_MISMATCH")
                url = f"https://api.github.com/repos/{repo}/releases/tags/{quote(tag, safe='')}"
                doc = store._text(url, ["release-note", repo, new_commit, tag], max_age=TTL)
                release = _object(doc)
                if (release.get("tag_name") != tag or release.get("draft") is not False
                        or release.get("prerelease") is not False):
                    raise EvidenceUnavailable("RELEASE_NOTE_IDENTITY_UNVERIFIED")
                body = release.get("body")
                if not isinstance(body, str):
                    raise EvidenceUnavailable("RELEASE_NOTE_BODY_UNAVAILABLE")
                result["documents"].append({
                    "source": _source(doc, "GITHUB_RELEASE", after, new_commit), "matchedVersions": [after],
                    "excerpts": _notes(body), "scope": "REPOSITORY_RELEASE",
                })
                break
            except EvidenceUnavailable as exc:
                gaps.append({"kind": "GITHUB_RELEASE", "reason": str(exc)})
                if str(exc) not in {"OFFICIAL_HTTP_404", "STALE_OR_MISSING_PIN_OFFLINE", "RELEASE_TAG_PUBLICATION_MISMATCH"}:
                    break
    result["unknowns"] = gaps
    return result


def _collect_batch(state, queries, *, offline, seconds):
    offline = (offline or external_network_evidence_disabled() or mirror_knowledge_only()
               or os.environ.get("DEPLOOM_MCP_MIGRATION_OFFLINE") == "1")
    store = SourceStore(Path(state) / "migration-evidence", deadline=time.monotonic() + seconds, offline=offline)
    rows = [collect_public_transition(store, *query) for query in queries]
    return {"rows": rows, "metrics": {"mode": "CACHE_ONLY" if offline else "ON_DEMAND", **store.metrics}}


def collect_workspace_releases(state, queries, *, offline):
    """Bound public DNS, transport and cache work with one watchdog per plan."""
    if offline:
        return _collect_batch(state, queries, offline=True, seconds=2)
    context = encoded({"state": str(state), "queries": queries}).decode("utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = run_process(
        [sys.executable, "-B", "-m", "deploom_mcp.migration_release", context],
        cwd=str(Path(__file__).parent), env=env, timeout=9, max_output_bytes=48 * 1024,
    )
    if result.timed_out or result.returncode:
        return {
            "rows": [{"status": "UNKNOWN", "reason": "OFFICIAL_WORKER_TIMEOUT" if result.timed_out
                      else "OFFICIAL_WORKER_UNAVAILABLE"} for _ in queries],
            "metrics": {"mode": "ON_DEMAND", "httpTransactions": None, "responseBodyBytesComplete": False},
        }
    output = json.loads(result.stdout)
    if len(output.get("rows", ())) != len(queries):
        raise EvidenceUnavailable("OFFICIAL_WORKER_PROTOCOL_INVALID")
    return output


if __name__ == "__main__":
    context = json.loads(sys.argv[1])
    print(json.dumps(_collect_batch(context["state"], context["queries"], offline=False, seconds=8),
                     ensure_ascii=False, allow_nan=False))
