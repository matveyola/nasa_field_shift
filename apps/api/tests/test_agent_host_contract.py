"""The API's constraint context against the agent's own contract (Task 05E, 2026-11-14).

The API validates the exported JSON Schema without importing the private agent package.
The expected shapes live in one fixture
shared with the agent's tests (services/agent/tests/fixtures/host_constraints.json): the API must
produce exactly those, and the agent's tests check its contract and behaviour on them. CI runs
this file when that fixture or the agent contract changes.
"""

# ruff: noqa: F811  (the `app` fixture comes from test_assistant_selected_run)

import json
from pathlib import Path

import pytest
from agent_contract_schema import validate_agent_wire
from fastapi.testclient import TestClient
from test_assistant_selected_run import _open_stream, _widget_turn, app  # noqa: F401
from test_planning_constraints import CONTEXT, URL, decision

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "services/agent/tests/fixtures/host_constraints.json"


# Fail, never skip: CI checks out the whole repository, so a missing file is a broken link.
def host_fixture() -> dict:
    assert FIXTURE.exists(), f"missing shared host-constraint fixture: {FIXTURE}"
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_api_signs_exactly_the_shared_host_constraints(app, monkeypatch):
    host = host_fixture()
    opened = _open_stream(app, monkeypatch)
    with TestClient(app) as client:
        assert client.post(URL, json=decision(*host["decided"])).status_code == 200
        context = client.get(CONTEXT.format("less_rain")).json()
        assert client.post(
            "/api/agent/assistant/F-03/2026/baseline/v1/agent/stream", json=_widget_turn()
        )
    assert context["constraints"] == host["signed"]
    # The published schema; installed agent tests additionally check strict semantic validators.
    validate_agent_wire("FieldContext", context)
    [request] = opened
    signed = validate_agent_wire("AgentRequest", json.loads(request.data))  # what the agent parses
    assert signed["field_context"]["constraints"] == host["signed"]
    body = request.data.decode()
    for words in host["farmer_words"]:
        assert words not in body
    assert "token" not in body and "decided_by" not in body


CONFIRMED = [
    ("irrigation_available", True),
    ("fertilizer_budget", "minimize_n"),
    ("erosion_protection", True),
    ("rotation_length_years", 5),
    ("excluded_crops", ["maize", "sunflower"]),
    ("preferred_crops", ["winter_wheat"]),
    ("previous_crop", "soybean"),
    ("organic_only", False),
    ("market_preference", ["sunflower"]),
]


@pytest.mark.parametrize(("ctype", "value"), CONFIRMED, ids=[c for c, _ in CONFIRMED])
@pytest.mark.parametrize("source", ["farmer_text", "agent_inferred"])
def test_every_kind_of_decision_reaches_the_agent_in_its_contract(app, ctype, value, source):
    decided = {"id": "c_1", "type": ctype, "value": value, "status": "confirmed", "source": source}
    decided |= (
        {"evidence": "the farmer's words"} if source == "farmer_text" else {"confidence": 0.7}
    )
    with TestClient(app) as client:
        assert client.post(URL, json=decision(decided)).status_code == 200
        context = client.get(CONTEXT.format("baseline")).json()
    parsed = validate_agent_wire("FieldContext", context)
    [constraint] = parsed["constraints"]
    assert (constraint["id"], constraint["type"], constraint["value"], constraint["status"]) == (
        f"pc_{ctype}",
        ctype,
        value,
        "confirmed",
    )
    if source == "agent_inferred":
        # An approved inference stays one.
        assert (constraint["source"], constraint.get("confidence")) == ("agent_inferred", 0.7)
    else:
        # The farmer's quote stays in the farm record, out of the signed context.
        assert (constraint["source"], constraint.get("evidence")) == ("farmer_ui", None)
