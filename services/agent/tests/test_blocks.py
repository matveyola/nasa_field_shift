"""Presentation blocks (metrics, table, bar chart, callout): every value is a verified fact, labels are words only."""

import pytest
from pydantic import ValidationError

from agent_service.llm.scripted import ScriptedProvider, call, say
from agent_service.llm_agent import LLMAgent
from contracts.models import AgentMessage

from .conftest import make_client, request_body, schema_validator

FACTS = ("get_field_facts", {"domains": ["soil", "water"], "fact_ids": []})
WIDGET = {"widget_version": "test", "capabilities": {"actions": [], "screens": []}}  # the widget always says what it can do


def answer(*blocks: dict, read_first: bool = True, text: str = "Ось головне про ґрунт і воду вашого поля.",
           client: dict = WIDGET) -> dict:
    """One scripted turn: read the facts (unless told not to), present the blocks, then a short text."""
    first = call(*([FACTS] if read_first else []), ("present_blocks", {"blocks": list(blocks)}))
    agent = LLMAgent(ScriptedProvider([first, say(text)]))
    with make_client(agent=agent) as c:
        response = c.post("/v1/agent/message", json=request_body(client=client))
    assert response.status_code == 200, response.text
    schema_validator("AgentMessage").validate(response.json())
    return response.json()


def shown(message: dict) -> list[dict]:
    return [b for b in message["blocks"] if b["type"] not in ("text", "clarifying_question")]


def test_metrics_block_is_shown_with_its_facts_and_the_demo_warning():
    m = answer({"type": "metrics", "title": "Ґрунт і вода", "fact_ids": ["f_ph_0_30", "{{fact:f_precip_2025_07}}"]})
    [block] = shown(m)
    assert block["title"] == "Ґрунт і вода" and block["fact_ids"] == ["f_ph_0_30", "f_precip_2025_07"]
    assert block["scenario"] is None  # no simulation values in it
    assert {"f_ph_0_30", "f_precip_2025_07"} <= {f["id"] for f in m["facts"]}  # the cite_as token form is accepted
    assert "fixture_data" in {w["code"] for w in m["warnings"]}


def test_table_and_callout_are_accepted_and_empty_titles_dropped():
    m = answer(
        {"type": "table", "title": "", "columns": ["Кислотність", "Глина"], "rows": [{"label": "Верхній шар", "fact_ids": ["f_ph_0_30", "f_clay_0_30"]}]},
        {"type": "callout", "tone": "warning", "text": "Це демо-поле: значення вигадані.", "fact_ids": []},
    )
    assert [b["type"] for b in shown(m)] == ["table", "callout"]
    assert shown(m)[0].get("title") is None


@pytest.mark.parametrize("block", [
    {"type": "metrics", "title": "", "fact_ids": ["f_does_not_exist"]},                    # not a fact of this turn
    {"type": "metrics", "title": "Вологість 21%", "fact_ids": ["f_ph_0_30"]},              # a number in a label
    {"type": "metrics", "title": "**Ґрунт**", "fact_ids": ["f_ph_0_30"]},                  # markdown in a label
    {"type": "bar_chart", "title": "Ґрунт", "bars": [{"label": "Кислотність", "fact_id": "f_ph_0_30"},
                                                      {"label": "Глина", "fact_id": "f_clay_0_30"}]},  # two units
    {"type": "callout", "tone": "warning", "text": "Дані з супутника у реальному часі.", "fact_ids": []},  # live claim
    {"type": "chart_js", "code": "alert(1)"},                                                # not a block type
])
def test_unsupported_blocks_are_refused(block):
    assert shown(answer(block)) == []


def test_facts_must_be_read_in_the_same_turn():
    """A ref listed in the app context is not enough: the value must have come back from get_field_facts."""
    assert shown(answer({"type": "metrics", "title": "", "fact_ids": ["f_ph_0_30"]}, read_first=False)) == []


def test_the_contract_refuses_blocks_that_show_facts_the_message_does_not_carry():
    base = {"turn_id": "t1", "locale": "uk", "meta": {"provider": "x", "model_id": "x", "prompt_version": "x", "latency_ms": 0}}
    with pytest.raises(ValidationError, match="not in the message"):
        AgentMessage.model_validate({**base, "blocks": [{"type": "text", "markdown": "a"},
                                                        {"type": "metrics", "fact_ids": ["f_ph_0_30"]}]})
    with pytest.raises(ValidationError, match="one fact id"):
        AgentMessage.model_validate({**base, "blocks": [{"type": "table", "columns": ["a", "b"],
                                                         "rows": [{"label": "x", "fact_ids": ["f1"]}]}]})


def test_a_comparison_table_names_its_scenario_set_by_the_server():
    """The demo model shows the rotation comparison as a table; the server, not the model, labels its scenario."""
    from .test_integrations import _agent, _fixture_registry, _turn
    from agent_service.llm.scripted import demo_script

    message = _turn(_agent(demo_script, _fixture_registry()), client=WIDGET)[-1][2]["message"]
    [table] = [b for b in message["blocks"] if b["type"] == "table"]
    assert table["scenario"] == "typical" and len(table["rows"]) == 2
    assert all(fid.startswith("sim.") for r in table["rows"] for fid in r["fact_ids"] if fid)
    schema_validator("AgentMessage").validate(message)


def test_a_metrics_card_is_kept_even_when_the_text_repeats_its_values():
    # Dropping such a card left a wall of text in live tests (2026-11-11): the card stays, the prompt asks the text
    # to explain it instead of repeating it.
    card = {"type": "metrics", "title": "Ґрунт", "fact_ids": ["f_ph_0_30", "f_precip_2025_07"]}
    m = answer(card, text="Кислотність {{fact:f_ph_0_30}}, опади {{fact:f_precip_2025_07}}.")
    assert [b["type"] for b in shown(m)] == ["metrics"] and {"f_ph_0_30", "f_precip_2025_07"} <= {f["id"] for f in m["facts"]}


def test_a_client_that_does_not_say_what_it_can_show_gets_text_and_questions_only():
    """The Field Shift API host (contract v0.1: no capabilities) reads only text and question blocks and refuses the
    whole reply otherwise: the agent does not offer present_blocks there, so the values go in the text."""
    from .test_integrations import _agent, _fixture_registry, _turn
    from agent_service.llm.scripted import demo_script

    m = answer({"type": "metrics", "title": "Ґрунт", "fact_ids": ["f_ph_0_30"]}, client={"widget_version": "field-shift-web"})
    assert shown(m) == [] and m["blocks"][0]["type"] == "text"
    message = _turn(_agent(demo_script, _fixture_registry()))[-1][2]["message"]  # the demo comparison, same host
    assert shown(message) == [] and any(f["id"].startswith("sim.") for f in message["facts"])
    schema_validator("AgentMessage").validate(message)
