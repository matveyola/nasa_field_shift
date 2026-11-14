"""Application-service guarantees: terminal events, timeouts, cancellation, output checks."""

import asyncio

import pytest

from agent_service.service import AgentService, ServiceError
from contracts.models import (
    AgentMessage, ErrorEvent, SetLayersAction, TextDeltaEvent, TurnCompletedEvent, TurnStartedEvent,
    UIActionEnvelope,
)

from .conftest import SCENARIO_B, ExplodingAgent, ScriptedAgent, SlowAgent, as_request, context, message_for, request_body

REQUEST = as_request(request_body("poltava"))


async def collect(service: AgentService, request=REQUEST) -> list:
    return [e async for e in service.stream(request)]


def assert_one_terminal_last(events: list) -> None:
    assert isinstance(events[0], TurnStartedEvent)
    terminal = [e for e in events if e.event in ("turn.completed", "error")]
    assert len(terminal) == 1 and events[-1] is terminal[0]


@pytest.mark.anyio
async def test_timeout_ends_with_retryable_error_and_closes_agent():
    agent = SlowAgent()
    events = await collect(AgentService(agent, turn_timeout_s=0.05))
    assert_one_terminal_last(events)
    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].code == "upstream_timeout" and events[-1].retryable
    assert agent.closed


@pytest.mark.anyio
async def test_cancelled_consumer_stops_the_agent():
    agent = SlowAgent()
    received = []

    async def consume():
        async for event in AgentService(agent, turn_timeout_s=60).stream(REQUEST):
            received.append(event)

    task = asyncio.create_task(consume())
    await asyncio.wait_for(agent.started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert agent.closed, "agent work must not keep running after the client goes away"
    assert not [e for e in received if e.event in ("turn.completed", "error")]


@pytest.mark.anyio
async def test_agent_exception_becomes_safe_internal_error():
    events = await collect(AgentService(ExplodingAgent(), turn_timeout_s=5))
    assert_one_terminal_last(events)
    error = events[-1]
    assert isinstance(error, ErrorEvent) and error.code == "internal" and not error.retryable
    assert error.request_id == REQUEST.request_id
    assert "hunter2" not in error.model_dump_json() and "secrets" not in error.model_dump_json()


@pytest.mark.anyio
async def test_agent_that_stops_without_a_message_is_an_error():
    agent = ScriptedAgent(lambda req, tid: [TextDeltaEvent(event="text.delta", text="hi")])
    events = await collect(AgentService(agent, turn_timeout_s=5))
    assert_one_terminal_last(events)
    assert isinstance(events[-1], ErrorEvent)


@pytest.mark.anyio
async def test_invented_fact_is_rejected():
    ctx = context("poltava")
    invented = dict(ctx["facts"][0], id="f_invented")
    agent = ScriptedAgent(lambda req, tid: [message_for(req, tid, facts=[invented],
                                                        warnings=[{"code": "fixture_data"}])])
    events = await collect(AgentService(agent, turn_timeout_s=5))
    assert isinstance(events[-1], ErrorEvent) and events[-1].code == "internal"


@pytest.mark.anyio
async def test_fixture_fact_cannot_be_upgraded_to_live():
    fact = context("poltava")["facts"][0]
    upgraded = dict(fact, provenance=dict(fact["provenance"], data_status="live"))
    agent = ScriptedAgent(lambda req, tid: [message_for(req, tid, facts=[upgraded])])
    events = await collect(AgentService(agent, turn_timeout_s=5))
    assert isinstance(events[-1], ErrorEvent)


@pytest.mark.anyio
async def test_constraint_evidence_must_be_the_farmers_words():
    request = as_request(request_body("kansas", SCENARIO_B))
    made_up = {"id": "k1", "type": "irrigation_available", "value": False, "status": "proposed",
               "source": "farmer_text", "evidence": "I never said this"}
    agent = ScriptedAgent(lambda req, tid: [message_for(req, tid, constraints_proposed=[made_up])])
    events = await collect(AgentService(agent, turn_timeout_s=5), request)
    assert isinstance(events[-1], ErrorEvent)


@pytest.mark.anyio
async def test_actions_referencing_unknown_layers_or_stale_revision_are_dropped():
    ctx = context("poltava")
    good = {"id": "a1", "field_context_revision": ctx["revision"], "trigger": "auto", "label": "ok",
            "action": {"type": "map.set_layers", "layer_ids": ["l_hls_ndvi"], "mode": "replace"}}
    unknown_layer = dict(good, id="a2", action=dict(good["action"], layer_ids=["l_does_not_exist"]))
    stale = dict(good, id="a3", field_context_revision=ctx["revision"] - 1)
    agent = ScriptedAgent(lambda req, tid: [message_for(req, tid, ui_actions=[good, unknown_layer, stale])])
    events = await collect(AgentService(agent, turn_timeout_s=5))
    assert isinstance(events[-1], TurnCompletedEvent)
    assert [a.id for a in events[-1].message.ui_actions] == ["a1"]


@pytest.mark.anyio
@pytest.mark.filterwarnings("ignore:Pydantic serializer warnings:UserWarning")  # expected: the model is corrupt
async def test_message_bypassing_validation_is_caught():
    """An agent that builds output with model_construct cannot smuggle an unknown action type."""
    def script(req, tid):
        bad = UIActionEnvelope.model_construct(
            id="a1", field_context_revision=req.field_context.revision, trigger="auto", label="x",
            action=SetLayersAction.model_construct(type="map.delete_field", layer_ids=["l_hls_ndvi"], mode="replace"),
        )
        msg = message_for(req, tid)
        return [AgentMessage.model_construct(**{**dict(msg), "ui_actions": [bad]})]

    events = await collect(AgentService(ScriptedAgent(script), turn_timeout_s=5))
    assert isinstance(events[-1], ErrorEvent)


@pytest.mark.anyio
async def test_respond_raises_service_error_with_public_fields_only():
    with pytest.raises(ServiceError) as info:
        await AgentService(ExplodingAgent(), turn_timeout_s=5).respond(REQUEST)
    assert info.value.error.code == "internal"
