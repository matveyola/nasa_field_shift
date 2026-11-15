"""Validate cross-service wire data, never import another service's source tree."""

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA = (
    Path(__file__).resolve().parents[3]
    / "services/agent/contracts/schema/fieldshift-contracts.schema.json"
)


@lru_cache
def validator(name: str) -> Draft202012Validator:
    bundle = json.loads(SCHEMA.read_text(encoding="utf-8"))
    schema = {"$ref": f"#/$defs/{name}", "$defs": bundle["$defs"]}
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_agent_wire(name: str, value: dict) -> dict:
    validator(name).validate(value)
    return value
