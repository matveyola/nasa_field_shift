import asyncio
import copy
import json
from collections.abc import Callable
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

from agent_service.agent import Agent
from agent_service.api import create_app
from agent_service.config import Settings
from contracts.export import SCHEMA_PATH
from contracts.models import AgentMessage, AgentRequest, TextDeltaEvent

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "contracts" / "fixtures"
BUNDLE = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

SCENARIO_A = "Що відомо про моє поле?"
SCENARIO_B = "У мене немає поливу, хочу зменшити витрати на добрива."
SCENARIO_C = "Покажи вологість ґрунту на карті."
SCENARIO_D = "Порівняй сівозміни під час посухи."
SCENARIO_E = "Яка кислотність ґрунту?"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def context(name: str) -> dict:
    return json.loads((FIXTURES / f"field_context.{name}.json").read_text(encoding="utf-8"))


def request_body(field: str | dict = "poltava", text: str | None = SCENARIO_A, **overrides) -> dict:
    body = {
        "request_id": "req_test",
        "session_id": "sess_test",
        "locale": "auto",
        "message": {"text": text} if text is not None else {"attachments": [{"attachment_id": "img1", "kind": "image"}]},
        "history": [],
        "field_context": copy.deepcopy(field) if isinstance(field, dict) else context(field),
        "client": {"widget_version": "test"},
    }
    body.update(overrides)
    return body


def as_request(body: dict) -> AgentRequest:
    return AgentRequest.model_validate_json(json.dumps(body, ensure_ascii=False))


def schema_validator(def_name: str) -> jsonschema.Draft202012Validator:
    """Validator for one type of the exported public JSON Schema (not the Pydantic models)."""
    return jsonschema.Draft202012Validator({"$ref": f"#/$defs/{def_name}", "$defs": BUNDLE["$defs"]})


def parse_sse(raw: str) -> list[tuple[str, str | None, dict]]:
    """Parse an SSE body into (event name, id, JSON data) frames; comment lines are skipped."""
    frames = []
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        name, frame_id, data = None, None, []
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            key, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if key == "event":
                name = value
            elif key == "id":
                frame_id = value
            elif key == "data":
                data.append(value)
        if data:
            frames.append((name, frame_id, json.loads("\n".join(data))))
    return frames


def make_client(settings: Settings | None = None, agent: Agent | None = None,
                factory: Callable[[Settings], Agent] | None = None, **kwargs) -> TestClient:
    settings = settings or Settings(environment="test")
    if agent is not None:
        factory = lambda _s: agent  # noqa: E731
    app = create_app(settings, factory) if factory else create_app(settings)
    return TestClient(app, **kwargs)


@pytest.fixture
def client():
    with make_client() as c:
        yield c


# ------------------------------------------------------------------ fake agents for failure paths


class SlowAgent:
    """Streams one delta, then hangs until cancelled. Records that it was closed."""

    name = "slow"

    def __init__(self):
        self.started = asyncio.Event()
        self.closed = False

    async def respond(self, request, turn_id):
        try:
            yield TextDeltaEvent(event="text.delta", text="thinking")
            self.started.set()
            await asyncio.sleep(3600)
        finally:
            self.closed = True


class ScriptedAgent:
    """Yields whatever `script(request, turn_id)` returns."""

    name = "scripted"

    def __init__(self, script):
        self.script = script

    async def respond(self, request, turn_id):
        for item in self.script(request, turn_id):
            yield item


class ExplodingAgent:
    name = "exploding"

    async def respond(self, request, turn_id):
        yield TextDeltaEvent(event="text.delta", text="partial")
        raise RuntimeError(r"db password=hunter2 at C:\internal\secrets.sqlite")


def message_for(request: AgentRequest, turn_id: str, **fields) -> AgentMessage:
    base = dict(
        turn_id=turn_id, locale="en", blocks=[{"type": "text", "markdown": "ok"}],
        meta={"provider": "test", "model_id": "none", "prompt_version": "t", "latency_ms": 0},
    )
    base.update(fields)
    return AgentMessage.model_validate_json(json.dumps(base, ensure_ascii=False, default=str))
