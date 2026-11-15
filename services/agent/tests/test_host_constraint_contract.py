"""The farmer's decisions as the API signs them into the agent's context (Task 05E, 2026-11-14).

`tests/fixtures/host_constraints.json` holds the exact constraints the API produces (apps/api/tests/
test_agent_host_contract.py checks the real API against it). Here: the agent's contract accepts exactly that shape and
refuses malformed variants, and propose_constraints treats confirmed, inferred-confirmed and rejected decisions the way
the host relies on.
"""

import copy
import json
from pathlib import Path

import pytest
from agent_service.llm.provider import ToolCall
from agent_service.llm.scripted import ScriptedProvider, call, say
from agent_service.llm_agent import LLMAgent
from agent_service.service import AgentService
from agent_service.tools import TurnState, default_registry, execute
from contracts.models import TurnCompletedEvent
from pydantic import ValidationError

from .conftest import as_request, request_body, schema_validator

HOST = json.loads(
    (Path(__file__).parent / "fixtures/host_constraints.json").read_text(
        encoding="utf-8"
    )
)
SIGNED = HOST["signed"]


def body_with(constraints: list[dict], text: str = "Що порадиш?") -> dict:
    body = request_body("kansas", text)  # kansas has no constraints of its own
    body["field_context"]["constraints"] = copy.deepcopy(constraints)
    return body


def state(text: str) -> TurnState:
    return TurnState(request=as_request(body_with(SIGNED, text)), turn_id="turn_test")


def prop(
    ctype: str,
    value,
    evidence: str = "",
    basis: str = "explicit",
    confidence: float = 1.0,
) -> dict:
    return {
        "type": ctype,
        "value": value,
        "basis": basis,
        "evidence": evidence,
        "confidence": confidence,
    }


async def propose(text: str, *proposals: dict) -> dict:
    result, _ = await execute(
        ToolCall("c", "propose_constraints", {"constraints": list(proposals)}),
        state(text),
        default_registry(with_knowledge=False),
        "c1",
        5,
    )
    return json.loads(result.content)


# ------------------------------------------------------------------ the contract


def test_the_signed_host_decisions_satisfy_the_agent_contract_and_schema():
    request = as_request(
        body_with(SIGNED)
    )  # pydantic, strict, as the agent parses a request
    assert [(c.id, c.status, c.source) for c in request.field_context.constraints] == [
        ("pc_excluded_crops", "confirmed", "farmer_ui"),
        ("pc_irrigation_available", "confirmed", "farmer_ui"),
        ("pc_organic_only", "rejected", "farmer_ui"),
        ("pc_rotation_length_years", "confirmed", "agent_inferred"),
    ]
    assert request.field_context.constraints[3].confidence == 0.6
    assert not list(
        schema_validator("FieldContext").iter_errors(body_with(SIGNED)["field_context"])
    )  # published schema


def test_the_signed_context_carries_no_farmer_words():
    signed = json.dumps(SIGNED, ensure_ascii=False)
    assert (
        not any(c.get("evidence") for c in SIGNED) and "machinery_limit" not in signed
    )
    assert not any(words in signed for words in HOST["farmer_words"])


def _mutated(index: int, **change) -> list[dict]:
    constraints = copy.deepcopy(SIGNED)
    constraints[index].update(change)
    return [{k: v for k, v in c.items() if v is not None} for c in constraints]


@pytest.mark.parametrize(
    "constraints",
    [
        _mutated(3, confidence=None),  # an approved inference without its confidence
        _mutated(1, source="farmer_text"),  # farmer words claimed without the quote
        _mutated(1, status="pending"),
        _mutated(1, type="irrigation"),
        _mutated(1, value="no"),
        _mutated(3, value=9),
        _mutated(0, value=["Sunflower!"]),
        _mutated(3, confidence=1.5),
        _mutated(
            1, token="0b9c6c6a-1f4e-4c7e-9f6e-2d3c4b5a6f70"
        ),  # the farmer's review token is not for the agent
        _mutated(1, decided_by=7),
        _mutated(0, id="pc_irrigation_available"),  # duplicate id
    ],
    ids=[
        "inferred_without_confidence",
        "farmer_text_without_evidence",
        "unknown_status",
        "unknown_type",
        "wrong_value_type",
        "rotation_out_of_range",
        "invalid_crop_id",
        "confidence_out_of_range",
        "review_token",
        "decided_by",
        "duplicate_id",
    ],
)
def test_malformed_host_constraints_are_refused(constraints):
    with pytest.raises(ValidationError):
        as_request(body_with(constraints))


# ------------------------------------------------------------------ how the agent treats each decision


@pytest.mark.anyio
async def test_a_confirmed_condition_is_already_in_context():
    body = await propose(
        "Не маю поливу, і соняшник не сіятиму.",
        prop("irrigation_available", False, "Не маю поливу"),
        prop("excluded_crops", ["sunflower"], "соняшник не сіятиму"),
    )
    assert body["accepted"] == [] and body["rejected"] == []
    assert body["already_in_context"] == [
        {"type": "irrigation_available", "existing_id": "pc_irrigation_available"},
        {"type": "excluded_crops", "existing_id": "pc_excluded_crops"},
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("ctype", "value"),
    [("irrigation_available", True), ("rotation_length_years", 5)],
    ids=["over_farmer_ui", "over_approved_inference"],
)
async def test_an_inference_never_replaces_a_confirmed_condition(ctype, value):
    body = await propose(
        "Схоже, щось змінилося.", prop(ctype, value, basis="inferred", confidence=0.7)
    )
    assert [r["category"] for r in body["rejected"]] == [
        "confirmed_by_farmer"
    ] and body["accepted"] == []


@pytest.mark.anyio
async def test_a_rejected_decision_is_not_a_confirmed_one():
    body = await propose(
        "Тільки органічне землеробство.",
        prop("organic_only", True, "Тільки органічне землеробство"),
    )
    [accepted] = body["accepted"]
    assert (
        accepted["value"] is True
        and "supersedes" not in accepted
        and "replaces_confirmed" not in accepted
    )
    assert body["already_in_context"] == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("text", "proposal", "replaced"),
    [
        (
            "Відтепер так: тепер у нас є полив.",
            prop("irrigation_available", True, "тепер у нас є полив"),
            {"id": "pc_irrigation_available", "value": False, "status": "confirmed"},
        ),
        (
            "Сівозміна на 3 роки, не більше.",
            prop("rotation_length_years", 3, "Сівозміна на 3 роки"),
            {"id": "pc_rotation_length_years", "value": 4, "status": "confirmed"},
        ),
    ],
    ids=["farmer_ui", "approved_inference"],
)
async def test_an_explicit_replacement_is_a_proposal_with_a_warning(
    text, proposal, replaced
):
    provider = ScriptedProvider(
        [
            call(("propose_constraints", {"constraints": [proposal]})),
            say(
                "Раніше ви підтвердили іншу умову. Пропоную зміну; вона запрацює після "
                "вашого підтвердження."
            ),
        ]
    )
    events = [
        e
        async for e in AgentService(LLMAgent(provider), 10).stream(
            as_request(body_with(SIGNED, text))
        )
    ]
    final = events[-1]
    assert isinstance(final, TurnCompletedEvent)
    [proposed] = final.message.constraints_proposed
    assert (proposed.value, proposed.status) == (
        proposal["value"],
        "proposed",
    )  # nothing is decided by the agent
    assert any(
        "replace one you confirmed" in (w.detail or "") for w in final.message.warnings
    )
    result = json.loads(provider.requests[1]["transcript"][-1].results[0].content)
    assert (
        result["accepted"][0]["supersedes"] == replaced
        and result["accepted"][0]["replaces_confirmed"] is True
    )
