"""Read an operator-pinned v2 private corpus without any refresh or execution."""
import hashlib
import os
import re
from collections import defaultdict
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .host_evidence_policy import mirror_knowledge_only
from .knowledge_contract_v2 import MAX_CORPUS_BYTES, SHA, validate_corpus
from .workspace_input_io import bound_directory
from .workspace_inventory import _read_bytes

DOMAIN = b"DepLoom private migration records v2\x00"
SOURCE = "verified-private-local"
_REPO = re.compile(r"https://[a-z0-9]+(?:[.-][a-z0-9]+)*(?:/[A-Za-z0-9_-][A-Za-z0-9_.-]*){2,8}/(?:-/)?blob/\Z")
_FIXTURE = r"([a-f0-9]{40})/(?:[a-z0-9_-]{1,64}/){1,8}check\.cjs\Z"


def load_snapshot(is_private):
    directory = os.environ.get("DEPLOOM_MCP_PRIVATE_KNOWLEDGE_DIR")
    if not mirror_knowledge_only() or not directory:
        return None
    root = Path(directory)
    key = os.environ.get("DEPLOOM_MCP_PRIVATE_KNOWLEDGE_PUBLIC_KEY", "")
    expected = os.environ.get("DEPLOOM_MCP_PRIVATE_KNOWLEDGE_SHA256", "")
    repository = os.environ.get("DEPLOOM_MCP_PRIVATE_KNOWLEDGE_SOURCE", "")
    if (not SHA.fullmatch(key) or not SHA.fullmatch(expected) or not _REPO.fullmatch(repository)
            or not root.is_absolute() or ".." in root.parts
            or (os.name == "nt" and not re.fullmatch(r"[a-zA-Z]:", root.drive))):
        raise ValueError("Invalid private migration knowledge configuration")
    if os.name == "nt":
        import ctypes
        if ctypes.windll.kernel32.GetDriveTypeW(str(root.anchor)) != 3:  # DRIVE_FIXED
            raise ValueError("Private migration knowledge requires a local drive")
    source_pattern = re.compile(re.escape(repository) + _FIXTURE)
    with bound_directory(Path(root.anchor), root) as parent:
        raw = _read_bytes(root / "records.json", MAX_CORPUS_BYTES, parent_binding=parent)
        signature = _read_bytes(root / "records.sig", 64, parent_binding=parent)
    digest = hashlib.sha256(raw)
    if digest.hexdigest() != expected or len(signature) != 64:
        raise ValueError("Invalid private migration knowledge digest or signature size")
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(signature, DOMAIN + digest.digest())
    records = defaultdict(list)
    for record in validate_corpus(raw, source_pattern=source_pattern)["records"]:
        if not is_private(record["package"]):
            raise ValueError("Private migration knowledge contains a non-private package")
        records[record["package"]].append(record)
    return records, source_pattern, expected


SNAPSHOT_ERRORS = (OSError, ValueError, InvalidSignature, RecursionError)
