import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from field_shift.api import create_app
from field_shift.config import Settings
from field_shift.db import build_session_factory
from field_shift.models import metadata
from field_shift.modules.agent.remote import RemoteAgentError, RemoteAgentProvider
from field_shift.seed import seed_demo

API_KEY = "a" * 32
SIGNING_KEY = "b" * 32


@pytest.fixture
def app():
    factory = build_session_factory("sqlite://")
    engine = factory.kw["bind"]
    metadata.create_all(engine)
    with factory() as session:
        seed_demo(session)
    application = create_app(
        Settings(
            agent_url="http://agent:8000", agent_api_key=API_KEY, agent_signing_key=SIGNING_KEY
        ),
        session_factory=factory,
    )
    try:
        yield application
    finally:
        engine.dispose()


def _decode(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@pytest.mark.parametrize(
    ("provider", "fixture_data", "status_demo", "reply_demo"),
    [
        pytest.param("scripted-llm", False, True, True, id="scripted"),
        pytest.param("openai", False, False, False, id="openai-compatible"),
        pytest.param("openai", True, False, True, id="openai-compatible-fixture"),
    ],
)
def test_signed_chat_uses_server_field_and_persists_safe_reply_metadata(
    app, monkeypatch, provider, fixture_data, status_demo, reply_demo
):
    calls = []

    def send(request, *, timeout, limit):
        if request.get_method() == "GET":
            return json.dumps({"status": "ready", "agent": f"llm/{provider}"}).encode()
        body = json.loads(request.data)
        field = body["field_context"]
        headers = {key.casefold(): value for key, value in request.header_items()}
        assert headers["authorization"] == f"Bearer {API_KEY}"
        version, encoded, signature = headers["x-fieldshift-context-attestation"].split(".")
        assert version == "v1"
        assert hmac.compare_digest(
            _decode(signature),
            hmac.digest(SIGNING_KEY.encode(), f"v1.{encoded}".encode(), "sha256"),
        )
        signed = json.loads(_decode(encoded))
        assert signed["body_sha256"] == hashlib.sha256(request.data).hexdigest()
        assert signed["field_id"] == field["field_id"] == "F-03"
        assert signed["field_context_revision"] == field["revision"] == 0
        assert signed["exp"] - signed["iat"] == 120
        assert field["area_ha"] > 1 and field["geometry"]["type"] == "Polygon"
        assert all(fact["id"] != "untrusted" for fact in field["facts"])
        assert body["locale"] == "uk"
        calls.append((body, signed["jti"]))
        return json.dumps(
            {
                "meta": {"provider": provider},
                "warnings": [{"code": "fixture_data"}] if fixture_data else [],
                "blocks": [
                    {"type": "text", "markdown": "Дані вибраного поля"},
                    {
                        "type": "clarifying_question",
                        "text": "Що відкрити?",
                        "options": ["Покажи NDVI"],
                    },
                ],
                "citations": [
                    {
                        "id": "1",
                        "title": "FAO 56",
                        "publisher": "FAO",
                        "year": 1998,
                        "url": "https://www.fao.org/4/x0490e/x0490e00.htm",
                    },
                    {
                        "id": "2",
                        "title": "Bad link",
                        "publisher": "Test",
                        "year": 2026,
                        "url": "https://www.fao.org@evil.example/",
                    },
                ],
                "ui_actions": [
                    {
                        "label": "Поле",
                        "field_context_revision": 0,
                        "action": {"type": "map.focus_field"},
                    },
                    {
                        "label": "Старе поле",
                        "field_context_revision": 1,
                        "action": {"type": "map.focus_field"},
                    },
                    {
                        "label": "Зберегти",
                        "field_context_revision": 0,
                        "action": {"type": "compare.set_constraints"},
                    },
                    {
                        "label": "Немає такого шару",
                        "field_context_revision": 0,
                        "action": {"type": "map.set_layers", "layer_ids": ["l_ndvi_variability"]},
                    },
                ],
            }
        ).encode()

    monkeypatch.setattr(app.state.agent_provider, "_request_json", send)
    payload = {
        "field_id": "F-03",
        "season": datetime.now(UTC).year,
        "scenario": "baseline",
        "message": "Що відомо?",
        "locale": "uk",
        "field_context": {"area_ha": 1, "facts": [{"id": "untrusted"}]},
    }
    with TestClient(app) as client:
        assert client.get("/api/agent/status").json() == {
            "provider": provider,
            "demo": status_demo,
            "available": True,
        }
        first = client.post("/api/agent/chat", json=payload)
        assert first.status_code == 200, first.text
        reply = first.json()
        assert reply["demo"] is reply_demo and reply["provider"] == provider
        assert reply["options"] == ["Покажи NDVI"]
        assert [citation["id"] for citation in reply["citations"]] == ["1"]
        assert [action["label"] for action in reply["actions"]] == ["Поле"]
        conversation_id = reply["conversation_id"]
        second = client.post(
            "/api/agent/chat", json={**payload, "conversation_id": conversation_id}
        )
        assert second.status_code == 200, second.text
        assert all(body["session_id"] == conversation_id for body, _ in calls)
        assert calls[0][1] != calls[1][1]
        assert [turn["role"] for turn in calls[1][0]["history"]] == ["user", "assistant"]
        thread = client.get(f"/api/agent/conversations/{conversation_id}").json()
        assert thread["messages"][-1]["provider"] == provider
        assert thread["messages"][-1]["demo"] is reply_demo
        assert thread["messages"][-1]["citations"] == reply["citations"]
        assert thread["messages"][-1]["options"] == reply["options"]
        mismatch = client.post(
            "/api/agent/chat",
            json={
                **payload,
                "field_id": "F-01",
                "conversation_id": conversation_id,
            },
        )
        assert mismatch.status_code == 409
        assert len(calls) == 2


def test_remote_failure_does_not_create_conversation_or_silently_fallback(app, monkeypatch):
    def fail(*args, **kwargs):
        raise RemoteAgentError(503, "The assistant is temporarily unavailable.")

    monkeypatch.setattr(app.state.agent_provider, "_request_json", fail)
    with TestClient(app) as client:
        assert client.get("/api/agent/status").json()["available"] is False
        response = client.post(
            "/api/agent/chat",
            json={
                "field_id": "F-03",
                "season": datetime.now(UTC).year,
                "scenario": "baseline",
                "message": "Forecast?",
            },
        )
        assert response.status_code == 503
        assert client.get("/api/agent/conversations", params={"field_id": "F-03"}).json() == []


def test_remote_turns_are_bounded_and_cannot_overlap_in_a_thread():
    provider = RemoteAgentProvider("http://agent:8000", API_KEY, SIGNING_KEY)
    provider._start("thread-a")
    with pytest.raises(RemoteAgentError, match="busy"):
        provider._start("thread-a")
    provider._start("thread-b")
    with pytest.raises(RemoteAgentError, match="busy"):
        provider._start("thread-c")


class _Stream:
    """A fake SSE response from the agent service."""

    def __init__(self, frames: bytes):
        self._frames = [frames[i : i + 7] for i in range(0, len(frames), 7)]
        self.closed = False

    class headers:  # noqa: N801
        @staticmethod
        def get_content_type():
            return "text/event-stream"

    def read1(self, _size):
        return self._frames.pop(0) if self._frames else b""

    def close(self):
        self.closed = True


ASSISTANT = "/api/agent/assistant/F-03/2026/baseline"


def test_assistant_widget_gets_the_server_built_field_context(app):
    with TestClient(app) as client:
        response = client.get(ASSISTANT + "/field-context?locale=uk")
    assert response.status_code == 200
    context = response.json()
    assert context["field_id"] == "F-03" and context["locale"] == "uk"
    assert context["geometry"]["type"] == "Polygon" and context["revision"] == 0


def test_assistant_stream_signs_a_server_context_and_passes_frames_through(app, monkeypatch):
    provider = app.state.agent_provider
    frames = b'event: turn.started\ndata: {"event":"turn.started"}\n\n'
    opened = []

    def open_(request, timeout):
        opened.append(request)
        return _Stream(frames)

    monkeypatch.setattr(provider._opener, "open", open_)
    widget = {
        "request_id": "req_1",
        "session_id": "ses_1",
        "locale": "uk",
        "message": {"text": "Що з вологою?"},
        "history": [],
        "client": {"widget_version": "w", "capabilities": {"actions": [], "screens": []}},
        "field_context": {"field_id": "F-03", "facts": [{"id": "untrusted"}]},
        "extra": "dropped",
    }
    with TestClient(app) as client:
        response = client.post(ASSISTANT + "/v1/agent/stream", json=widget)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.content == frames
    [request] = opened
    body = json.loads(request.data)
    assert set(body) == {*widget} - {"extra"}
    assert body["field_context"]["geometry"]["type"] == "Polygon"
    assert all(fact.get("id") != "untrusted" for fact in body["field_context"]["facts"])
    headers = {key.casefold(): value for key, value in request.header_items()}
    assert headers["accept"] == "text/event-stream"
    _, encoded, _ = headers["x-fieldshift-context-attestation"].split(".")
    signed = json.loads(_decode(encoded))
    assert signed["body_sha256"] == hashlib.sha256(request.data).hexdigest()
    assert signed["field_id"] == "F-03"
    assert provider._inflight == set()  # the turn slot is released after the stream


def test_assistant_health_shows_only_what_the_widget_needs(app, monkeypatch):
    provider = app.state.agent_provider

    def send(request, *, timeout, limit):
        return json.dumps(
            {
                "status": "ready",
                "agent": "llm/gemini",
                "model": "gemini-3.8-flash",
                "knowledge": {"mode": "lexical", "path": "/secret"},
                "integrations": {"simulation": {"connected": False, "url": "http://x"}},
            }
        ).encode()

    monkeypatch.setattr(provider, "_request_json", send)
    with TestClient(app) as client:
        response = client.get(ASSISTANT + "/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["agent"] == "llm/gemini" and body["knowledge"] == {"mode": "lexical"}
    assert "url" not in json.dumps(body) and "/secret" not in json.dumps(body)


def _signed_stream_request(app, monkeypatch, widget_client, principal=None):
    provider = app.state.agent_provider
    opened = []
    monkeypatch.setattr(
        provider._opener, "open", lambda request, timeout: opened.append(request) or _Stream(b"")
    )
    payload = {
        "request_id": "req_1",
        "session_id": "ses_1",
        "message": {"text": "x"},
        "history": [],
        "client": widget_client,
    }
    context = {"field_id": "F-03", "revision": 0}
    args = (payload, context) if principal is None else (payload, context, principal)
    b"".join(provider.stream(*args))
    [request] = opened
    headers = {key.casefold(): value for key, value in request.header_items()}
    _, encoded, _ = headers["x-fieldshift-context-attestation"].split(".")
    return json.loads(request.data), json.loads(_decode(encoded))


def test_capabilities_a_browser_claims_beyond_the_host_are_not_signed(app, monkeypatch):
    claimed = {
        "widget_version": "w",
        "capabilities": {
            "actions": [
                "map.set_layers",
                "compare.set_constraints",
                "scenario.set",
                "panel.open",
                "map.set_layers",
                "chart.focus",
                "app.open_screen",
                "map.focus_field",
            ],
            "screens": [
                {"screen_id": "water", "label": "Вода"},
                {"screen_id": "admin", "label": "Admin"},
            ],
        },
    }
    body, _ = _signed_stream_request(app, monkeypatch, claimed)
    caps = body["client"]["capabilities"]
    host_order = ["map.set_layers", "chart.focus", "app.open_screen", "map.focus_field"]
    assert caps["actions"] == host_order
    assert [s["screen_id"] for s in caps["screens"]] == ["water"]
    assert body["client"]["widget_version"] == "w"


def test_a_client_without_capabilities_stays_a_text_only_client(app, monkeypatch):
    body, _ = _signed_stream_request(app, monkeypatch, {"widget_version": "w"})
    assert body["client"] == {"widget_version": "w"}


def test_local_mode_signs_the_shared_workspace_and_team_mode_the_signed_in_member(app, monkeypatch):
    from types import SimpleNamespace

    from field_shift.modules.access.service import Principal
    from field_shift.modules.agent.routes import _principal
    from field_shift.modules.agent.signing import LOCAL_PRINCIPAL, agent_principal

    def request_as(principal):
        return SimpleNamespace(state=SimpleNamespace(principal=principal))

    local = request_as(Principal(None, 1, "Local", None, "admin"))
    member = request_as(Principal(7, 1, "Ann", "a@x", "editor"))
    assert _principal(local) == LOCAL_PRINCIPAL == "field-shift-local-farm"
    assert _principal(member) == agent_principal(7, 1) == "farm-1:user-7"
    assert _principal(SimpleNamespace(state=SimpleNamespace())) == LOCAL_PRINCIPAL

    _, signed = _signed_stream_request(app, monkeypatch, {"widget_version": "w"})
    assert signed["principal"] == LOCAL_PRINCIPAL  # unchanged single-farm behaviour
    _, signed = _signed_stream_request(app, monkeypatch, {"widget_version": "w"}, "farm-1:user-7")
    assert signed["principal"] == "farm-1:user-7"


def test_the_assistant_stream_route_attests_the_caller_of_the_request(app, monkeypatch):
    provider = app.state.agent_provider
    seen = []
    monkeypatch.setattr(
        provider, "stream", lambda payload, context, who: seen.append(who) or iter([b""])
    )
    with TestClient(app) as client:
        client.post(ASSISTANT + "/v1/agent/stream", json={"session_id": "s", "client": {}})
    assert seen == ["field-shift-local-farm"]  # local access mode: the shared workspace
