"""App screens and what the app can do: the agent opens only what the host declared, and otherwise says where to go."""

import json

from agent_service.llm.provider import ToolResults
from agent_service.llm.scripted import ScriptedProvider, call, say
from agent_service.llm_agent import LLMAgent

from .conftest import make_client, request_body, schema_validator

CAPS = {"actions": ["app.open_screen", "map.focus_field"],
        "screens": [{"screen_id": "soil", "label": "Ґрунт", "how_to_open": "Меню → Поле → Ґрунт"},
                    {"screen_id": "fields", "label": "Мої поля"}]}


def turn(action: dict, caps: dict | None = CAPS) -> tuple[dict, dict]:
    """One scripted turn proposing `action`; returns the message and the tool result the model saw."""
    seen: dict = {}

    def answer(transcript, _tools):
        seen.update(json.loads(next(i for i in reversed(transcript) if isinstance(i, ToolResults)).results[0].content))
        return say("Готово.")

    agent = LLMAgent(ScriptedProvider([call(("propose_ui_actions", {"actions": [action]})), answer]))
    client = {"widget_version": "test", **({"capabilities": caps} if caps is not None else {})}
    with make_client(agent=agent) as c:
        response = c.post("/v1/agent/message", json=request_body(client=client))
    assert response.status_code == 200, response.text
    schema_validator("AgentMessage").validate(response.json())
    return response.json(), seen


def open_screen(screen_id: str) -> dict:
    return {"type": "app.open_screen", "screen_id": screen_id, "trigger": "auto", "label": "Відкрити екран", "reason": ""}


def test_a_declared_screen_opens_at_once():
    message, _ = turn(open_screen("soil"))
    [env] = message["ui_actions"]
    assert env["action"] == {"type": "app.open_screen", "screen_id": "soil"} and env["trigger"] == "auto"


def test_an_unknown_screen_is_refused_with_the_apps_own_directions():
    message, seen = turn(open_screen("settings"))
    assert message["ui_actions"] == []
    [r] = seen["rejected"]
    assert r["category"] == "unknown_screen" and "Меню → Поле → Ґрунт" in r["reason"]


def test_an_action_the_app_cannot_do_is_refused():
    layers = {"type": "map.set_layers", "layer_ids": ["l_smap_rootzone"], "mode": "replace", "trigger": "auto",
              "label": "Вологість", "reason": ""}
    message, seen = turn(layers)
    assert message["ui_actions"] == [] and seen["rejected"][0]["category"] == "not_in_app"
    message, _ = turn(layers, caps=None)  # an older widget that does not say: checked against the field only
    assert [a["action"]["type"] for a in message["ui_actions"]] == ["map.set_layers"]


def test_the_model_sees_what_the_app_can_show():
    seen: dict = {}

    def look(transcript, _tools):
        seen["context"] = transcript[0].text
        return say("Привіт.")

    with make_client(agent=LLMAgent(ScriptedProvider([look]))) as c:
        c.post("/v1/agent/message", json=request_body(client={"widget_version": "t", "capabilities": CAPS}))
    assert '"app": {"can": ["app.open_screen", "map.focus_field"]' in seen["context"]
    assert "Меню → Поле → Ґрунт" in seen["context"]
