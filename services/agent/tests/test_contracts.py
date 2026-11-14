import copy
import json
import typing
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from contracts import models as m
from contracts.export import SCHEMA_PATH, build

FIXTURES = Path(__file__).parents[1] / "contracts" / "fixtures"

# fixture file prefix -> type it must validate as
FIXTURE_TYPES = {
    "field_context": m.FieldContext,
    "agent_request": m.AgentRequest,
    "agent_message": m.AgentMessage,
    "agent_events": list[m.AgentEvent],
    "rotation_comparison": m.RotationComparison,
    "timeseries": m.Timeseries,
}


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def validate(type_, data) -> object:
    # JSON mode on purpose: contracts arrive as JSON, and strict mode only parses dates from JSON.
    return TypeAdapter(type_).validate_json(json.dumps(data, ensure_ascii=False))


def fixture_files():
    return sorted(p.name for p in FIXTURES.glob("*.json"))


@pytest.mark.parametrize("name", fixture_files())
def test_fixture_validates(name):
    validate(FIXTURE_TYPES[name.split(".")[0]], load(name))


@pytest.mark.parametrize("name", fixture_files())
def test_fixture_never_claims_real_data(name):
    statuses = []

    def walk(node):
        if isinstance(node, dict):
            if "data_status" in node:
                statuses.append(node["data_status"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(load(name))
    assert set(statuses) <= {"fixture", "unavailable"}


def test_every_fixture_type_is_covered():
    assert {n.split(".")[0] for n in fixture_files()} == set(FIXTURE_TYPES)


def test_field_profile_is_a_slice_of_field_context():
    ctx = load("field_context.poltava.json")
    keys = ("field_id", "facts", "timeseries_index", "available_layers")
    validate(m.FieldProfile, {k: ctx[k] for k in keys})


def test_constraint_type_list_matches_constraint_models():
    members = typing.get_args(typing.get_args(m.Constraint.__value__)[0])
    tags = {typing.get_args(cls.model_fields["type"].annotation)[0] for cls in members}
    assert tags == set(typing.get_args(m.ConstraintType))


def test_schema_export_is_current():
    assert SCHEMA_PATH.read_text(encoding="utf-8") == build(), "run: uv run python -m contracts.export"


# ------------------------------------------------------------- invalid samples


def put(*path, value):
    def mutate(d):
        for key in path[:-1]:
            d = d[key]
        d[path[-1]] = value
    return mutate


def drop(*path):
    def mutate(d):
        for key in path[:-1]:
            d = d[key]
        del d[path[-1]]
    return mutate


def append(*path, value):
    def mutate(d):
        for key in path:
            d = d[key]
        d.append(value)
    return mutate


CTX = "field_context.poltava.json"
MSG = "agent_message.poltava.json"
ROT = "rotation_comparison.poltava.json"
SAT = ("facts", 0)  # satellite_product fact with a quantity
ML = ("facts", 6)   # ml_estimate fact

INVALID = {
    "unknown ui action type": (MSG, put("ui_actions", 0, "action", "type", value="map.delete_field"), "does not match any of the expected tags"),
    "fact without provenance": (CTX, drop(*SAT, "provenance"), "Field required"),
    "satellite product without version": (CTX, drop(*SAT, "provenance", "source", "version"), "Field required"),
    "satellite product without source": (CTX, put(*SAT, "provenance", "source", value=None), "requires source"),
    "ml estimate without model": (CTX, drop(*ML, "provenance", "model"), "requires model"),
    "farmer text constraint without evidence": (CTX, drop("constraints", 0, "evidence"), "evidence span"),
    "unknown unit": (CTX, put(*SAT, "quantity", "unit", value="furlong"), "Input should be"),
    "non-finite value": (CTX, put(*SAT, "quantity", "value", value=float("nan")), "finite"),
    "number sourced from LLM": (CTX, put(*SAT, "provenance", value={"kind": "llm_explanation", "data_status": "fixture", "retrieved_at": "2026-11-06T09:00:00Z"}), "LLM output"),
    "unavailable fact with a value": (CTX, put(*SAT, "provenance", "data_status", value="unavailable"), "must not carry a value"),
    "non-UTC timestamp": (CTX, put(*SAT, "provenance", "retrieved_at", value="2026-11-06T11:00:00+02:00"), "UTC"),
    "unknown extra field": (CTX, put("farm_name", value="x"), "Extra inputs are not permitted"),
    "ui_state references unknown layer": (CTX, append("ui_state", "active_layer_ids", value="l_missing"), "unknown layer"),
    "chart references unknown metric": (CTX, append("available_charts", 0, "metric_ids", value="m_missing"), "unknown metric"),
    "duplicate fact id": (CTX, put("facts", 1, "id", value="f_sm_rootzone_2025_07"), "duplicate fact"),
    "open polygon ring": (CTX, put("geometry", "coordinates", 0, value=[[34.54, 49.6], [34.548, 49.6], [34.548, 49.605], [34.54, 49.605]]), "closed"),
    "reversed time range": (CTX, put("ui_state", "time_range", value={"start": "2025-10-31", "end": "2025-03-01"}), "start must be <= end"),
    "string where number expected": (CTX, put(*SAT, "quantity", "value", value="0.21"), "Input should be a valid number"),
    "fixture facts without warning": (MSG, put("warnings", value=[]), "fixture_data"),
    "citation url not http": (MSG, put("citations", 0, "url", value="javascript:alert(1)"), "URL"),
    "score from satellite product": (ROT, put("rotations", 0, "scores", "water_risk", "provenance", value={"kind": "satellite_product", "data_status": "fixture", "source": {"provider": "x", "product_id": "y", "version": "1"}, "retrieved_at": "2026-11-06T09:00:00Z"}), "deterministic_calc or ml_estimate"),
    "rotation cites unknown fact": (ROT, append("rotations", 0, "reason_fact_ids", value="f_missing"), "unknown fact"),
    "duplicate rotation rank": (ROT, put("rotations", 1, "rank", value=1), "ranks must be unique"),
}


@pytest.mark.parametrize("case", INVALID)
def test_invalid_sample_rejected(case):
    name, mutate, expected = INVALID[case]
    data = copy.deepcopy(load(name))
    mutate(data)
    with pytest.raises(ValidationError, match=expected):
        validate(FIXTURE_TYPES[name.split(".")[0]], data)


def test_empty_user_message_rejected():
    req = load("agent_request.kansas.json")
    req["message"] = {}
    with pytest.raises(ValidationError, match="text or attachments"):
        validate(m.AgentRequest, req)
