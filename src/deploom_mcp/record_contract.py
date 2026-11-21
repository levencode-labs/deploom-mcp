"""The single closed JSON Schema consumed by intake, publication and MCP."""
import json
from functools import lru_cache
from jsonschema import Draft202012Validator

PACKAGES = ("react", "react-dom", "react-test-renderer", "vite", "typescript", "eslint", "semver")
CHECKS = ("install", "build", "test", "lint", "typecheck", "peer-compatibility")
RECIPES = {
    "align-react-renderers": "Check the target renderer peer ranges, then run the recipient's renderer tests.",
    "peer-companion": "Read the target package peer range and run npm ls on the recipient's installed tree.",
    "eslint-flat-config": "Check configuration format and ignore patterns, then run the recipient's lint task.",
}
VERSION = r"(?:0|[1-9][0-9]{0,7})(?:\.(?:0|[1-9][0-9]{0,7})){2}"
SOURCE = r"https://github\.com/levencode-labs/deploom-npm/blob/([a-f0-9]{40})/(fixtures/[a-z0-9_-]{1,64}/check\.cjs)"
KINDS = ("history", "knowledge")
LIMIT = 2_000_000


def parse(raw):
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("Duplicate JSON key")
        return result
    if not isinstance(raw, bytes) or len(raw) > LIMIT:
        raise ValueError("Invalid cache size")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except RecursionError as exc:
        raise ValueError("Invalid cache nesting") from exc


@lru_cache(maxsize=2)
def validator(kind):
    if kind not in KINDS:
        raise ValueError("Unknown record kind")
    properties = {"schema": {"type": "integer", "const": 1},
                  "package": {"type": "string", "enum": list(PACKAGES), "maxLength": 32},
                  "source": {"type": "string", "maxLength": 200, "pattern": "^" + SOURCE + "$"}}
    for key in ("from", "to"):
        properties[key] = {"type": "string", "maxLength": 26, "pattern": "^" + VERSION + "$"}
    enums = {"check": CHECKS, "outcome": ("passed", "failed")} if kind == "history" else {"recipe": tuple(RECIPES)}
    for key, values in enums.items():
        properties[key] = {"type": "string", "maxLength": 32, "enum": list(values)}
    return Draft202012Validator({"type": "array", "maxItems": 2000, "items": {
        "type": "object", "properties": properties, "additionalProperties": False,
        "required": list(properties)}})


def validate(kind, rows):
    if next(validator(kind).iter_errors(rows), None) is not None:
        raise ValueError("Record does not satisfy the public JSON Schema")
    seen = set()
    for row in rows:
        if (type(row["schema"]) is not int or row["from"] == row["to"] or
                not all(v.isascii() and v.isprintable() for v in row.values() if isinstance(v, str))):
            raise ValueError("Invalid version transition or text")
        identity = (row["package"], row["from"], row["to"], row["source"], row.get("check", row.get("recipe")))
        if identity in seen:
            raise ValueError("Conflicting or duplicate observations")
        seen.add(identity)
    return rows
