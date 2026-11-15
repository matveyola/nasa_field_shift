"""Export the contract JSON Schema bundle.

    uv run python -m contracts.export --output-dir contracts/schema
    uv run python -m contracts.export --check  # exit 1 if the committed file is stale
"""

import argparse
import json
import sys
from pathlib import Path

from . import integration as i
from . import models as m
from .resources import SCHEMAS

SCHEMA_PATH = SCHEMAS.joinpath("fieldshift-contracts.schema.json")
INTEGRATION_SCHEMA_PATH = SCHEMAS.joinpath("fieldshift-integration.schema.json")


class FieldShiftContracts(m.Model):
    """Index of the top-level contract types. Export root only; never sent over the wire."""

    field_context: m.FieldContext
    field_profile: m.FieldProfile
    agent_request: m.AgentRequest
    agent_event: m.AgentEvent
    agent_message: m.AgentMessage
    ui_action_envelope: m.UIActionEnvelope
    error_envelope: m.ErrorEnvelope
    timeseries_query: m.TimeseriesQuery
    timeseries: m.Timeseries
    compare_rotations_request: m.CompareRotationsRequest
    rotation_comparison: m.RotationComparison


class FieldShiftIntegrationContracts(m.Model):
    """Index of the service integration envelopes (PROPOSED v0.1). Export root only."""

    capabilities_response: i.CapabilitiesResponse
    field_profile_request: i.FieldProfileRequest
    field_profile_response: i.FieldProfileResponse
    comparison_request: i.ComparisonRequest
    comparison_response: i.ComparisonResponse
    integration_error: i.IntegrationError


_TS_SCALARS = {"number": "number", "integer": "number", "string": "string", "null": "null"}


def _ts_type(node: dict) -> str:
    if "anyOf" in node:
        return " | ".join(_ts_type(n) for n in node["anyOf"])
    return _TS_SCALARS[node["type"]]  # KeyError = a new tuple shape: extend _TS_SCALARS


def _postprocess(node: object) -> None:
    """Make the schema friendly to json-schema-to-typescript.

    - Drop per-property titles: json2ts turns each one into its own exported alias.
    - Add a `tsType` hint to tuples: json2ts does not understand `prefixItems`
      (JSON Schema 2020-12) and would emit `[unknown, unknown]`.
    - Drop the OpenAPI-style `discriminator`: strict Ajv rejects it, and `oneOf`
      already validates exactly because every branch pins its tag with `const`.
    """
    if isinstance(node, dict):
        node.pop("discriminator", None)
        for prop in node.get("properties", {}).values():
            prop.pop("title", None)
        if "prefixItems" in node:
            node["tsType"] = "[" + ", ".join(_ts_type(n) for n in node["prefixItems"]) + "]"
        for value in node.values():
            _postprocess(value)
    elif isinstance(node, list):
        for value in node:
            _postprocess(value)


def build(root: type[m.Model] = FieldShiftContracts, comment: str | None = None) -> str:
    schema = root.model_json_schema()
    _postprocess(schema)
    schema["$comment"] = comment or f"Field Shift contracts v{m.SCHEMA_VERSION}. Generated from contracts/models.py; do not edit."
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def build_integration() -> str:
    return build(FieldShiftIntegrationContracts,
                 f"Field Shift service integration contracts v{i.INTEGRATION_VERSION} (PROPOSED). "
                 "Generated from contracts/integration.py; do not edit.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check bundled schemas or --output-dir")
    parser.add_argument("--output-dir", type=Path, help="explicit directory for generated schemas")
    args = parser.parse_args()
    if not args.check and args.output_dir is None:
        parser.error("writing requires --output-dir; installed package resources are read-only")
    directory = args.output_dir if args.output_dir is not None else SCHEMAS
    outputs = {directory / SCHEMA_PATH.name: build(), directory / INTEGRATION_SCHEMA_PATH.name: build_integration()}
    if args.check:
        stale = [p for p, text in outputs.items() if (p.read_text(encoding="utf-8") if p.is_file() else "") != text]
        for p in stale:
            print(f"{p} is stale: regenerate with `python -m contracts.export --output-dir DIRECTORY`", file=sys.stderr)
        if stale:
            return 1
        print("schema up to date")
        return 0
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for p, text in outputs.items():
        p.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
