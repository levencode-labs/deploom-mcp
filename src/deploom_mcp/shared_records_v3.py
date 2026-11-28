"""Scoped knowledge uses its own endpoint, signature domain and rollback history.

The v2 endpoint stays compatible with deployed strict v2 readers. Both editions
share byte verification and atomic storage code, with no mutable global mode.
"""
from . import shared_records_v2 as v2

BASE = "https://levencode-labs.github.io/deploom-npm/v3/"
DOMAIN = b"DepLoom public migration records v3\x00"
DB = "signed-records-v3.sqlite"
SCHEMA = 3
ERRORS = v2.ERRORS
verified_shard = v2.verified_shard


def verified_index(raw, signature):
    return v2.verified_index(raw, signature, domain=DOMAIN, schema=SCHEMA)


def _read(db):
    return v2._read(db, domain=DOMAIN, schema=SCHEMA)


def refresh(root, fetch=v2.get, *, if_due=False):
    return v2.refresh(root, fetch, if_due=if_due, base=BASE, database=DB, domain=DOMAIN, schema=SCHEMA)


def refresh_from_mirror(root, read, *, if_due=False):
    return v2.refresh_from_mirror(root, read, if_due=if_due, database=DB, domain=DOMAIN, schema=SCHEMA)


def refresh_preferred(root, fetch=v2.get, *, if_due=False):
    """Try scoped knowledge first; a missing edition may still refresh legacy v2."""
    return refresh(root, fetch, if_due=if_due) or v2.refresh(root, fetch, if_due=if_due)


def lookup(root, package, from_version, to_version, *, manager="npm"):
    return v2.lookup(root, package, from_version, to_version, manager=manager,
                     database=DB, domain=DOMAIN, schema=SCHEMA)
