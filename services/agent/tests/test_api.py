"""HTTP API: health, validation, auth, CORS, limits, errors, SSE protocol, public-schema compatibility."""

import asyncio
import copy
import json

import anyio
import pytest

from agent_service.api import create_app
from agent_service.config import Settings

from .conftest import (
    ROOT, SCENARIO_B, SCENARIO_C, ExplodingAgent, SlowAgent, context, make_client, parse_sse, request_body,
    schema_validator,
)

MESSAGE = "/v1/agent/message"
STREAM = "/v1/agent/stream"
ERROR_ENVELOPE = schema_validator("ErrorEnvelope")


def assert_error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    body = response.json()
    ERROR_ENVELOPE.validate(body)
    assert body["error"]["code"] == code
    assert body["error"]["request_id"] == response.headers["x-request-id"]
    return body["error"]


# ------------------------------------------------------------------ health


def test_liveness(client):
    assert client.get("/health/live").json() == {"status": "ok"}


def test_readiness_when_agent_initialised(client):
    response = client.get("/health/ready")
    assert response.status_code == 200 and response.json()["status"] == "ready"


def test_readiness_reflects_agent_init_failure():
    def broken_factory(_settings):
        raise RuntimeError("model weights missing at /opt/secret")

    with make_client(factory=broken_factory) as c:
        assert c.get("/health/live").status_code == 200
        ready = c.get("/health/ready")
        assert ready.status_code == 503 and "secret" not in ready.text
        error = assert_error(c.post(MESSAGE, json=request_body()), 503, "upstream_unavailable")
        assert error["retryable"] is True


# ------------------------------------------------------------------ the happy path


def test_valid_request_returns_contract_valid_message(client):
    response = client.post(MESSAGE, json=request_body("poltava", SCENARIO_C))
    assert response.status_code == 200
    schema_validator("AgentMessage").validate(response.json())
    assert response.headers["content-type"].startswith("application/json")


def test_request_id_header_is_echoed_or_generated(client):
    assert client.get("/health/live", headers={"X-Request-ID": "abc-123"}).headers["x-request-id"] == "abc-123"
    generated = client.get("/health/live", headers={"X-Request-ID": "bad id\nwith newline"}).headers["x-request-id"]
    assert generated != "bad id\nwith newline" and len(generated) == 32


# ------------------------------------------------------------------ input validation


def mutated(mutate) -> dict:
    body = request_body("poltava")
    mutate(body)
    return body


INVALID_BODIES = {
    "missing field_context": lambda b: b.pop("field_context"),
    "unknown top-level key (e.g. actions smuggled into a request)": lambda b: b.update(
        ui_actions=[{"type": "map.delete_field"}]),
    "unknown constraint type": lambda b: b["field_context"]["constraints"].append(
        {"id": "k9", "type": "delete_everything", "value": True, "status": "proposed", "source": "farmer_ui"}),
    "ui_state references unknown layer": lambda b: b["field_context"]["ui_state"]["active_layer_ids"].append("l_nope"),
    "chart references unknown metric": lambda b: b["field_context"]["available_charts"][0]["metric_ids"].append("m_nope"),
    "latitude out of range": lambda b: b["field_context"].update(centroid=[34.5, 123.0]),
    "coordinates are not numbers": lambda b: b["field_context"].update(centroid=["34.5", "49.6"]),
    "polygon ring not closed": lambda b: b["field_context"]["geometry"]["coordinates"][0].pop(),
    "unknown locale": lambda b: b.update(locale="fr"),
    "empty message": lambda b: b.update(message={}),
    "history too long": lambda b: b.update(history=[{"role": "user", "text": "x"}] * 11),
    "non-UTC timestamp": lambda b: b["field_context"]["facts"][0]["provenance"].update(
        retrieved_at="2026-11-06T11:00:00+02:00"),
}


@pytest.mark.parametrize("case", INVALID_BODIES)
def test_invalid_requests_are_rejected(client, case):
    error = assert_error(client.post(MESSAGE, json=mutated(INVALID_BODIES[case])), 422, "invalid_request")
    assert 0 < len(error["detail"]) <= 300


def test_validation_errors_do_not_echo_farmer_text(client):
    body = request_body("poltava", "SECRET-FARM-NOTE")
    body["locale"] = "fr"
    response = client.post(MESSAGE, json=body)
    assert response.status_code == 422 and "SECRET-FARM-NOTE" not in response.text


def test_malformed_json_is_rejected(client):
    assert_error(client.post(MESSAGE, content=b"{not json", headers={"content-type": "application/json"}),
                 422, "invalid_request")


def test_non_json_content_type_is_rejected(client):
    assert_error(client.post(MESSAGE, content=json.dumps(request_body()), headers={"content-type": "text/plain"}),
                 415, "invalid_request")


def test_body_size_limit_with_and_without_content_length():
    with make_client(Settings(environment="test", max_body_bytes=4096)) as c:
        big = json.dumps(request_body("poltava")).encode()  # ~9 KB
        assert_error(c.post(MESSAGE, content=big, headers={"content-type": "application/json"}), 413, "payload_too_large")

        def chunked():  # no Content-Length header: the limit must still hold
            for i in range(0, len(big), 1000):
                yield big[i:i + 1000]

        assert_error(c.post(MESSAGE, content=chunked(), headers={"content-type": "application/json"}),
                     413, "payload_too_large")


# ------------------------------------------------------------------ errors


def test_unknown_route_uses_error_envelope(client):
    assert_error(client.get("/v1/nope"), 404, "invalid_request")


def test_agent_failure_hides_internals():
    with make_client(agent=ExplodingAgent()) as c:
        response = c.post(MESSAGE, json=request_body())
        assert_error(response, 500, "internal")
        for leaked in ("hunter2", "secrets.sqlite", "Traceback", "RuntimeError"):
            assert leaked not in response.text


def test_turn_timeout_maps_to_504():
    with make_client(Settings(environment="test", turn_timeout_s=0.05), agent=SlowAgent()) as c:
        assert_error(c.post(MESSAGE, json=request_body()), 504, "upstream_timeout")


# ------------------------------------------------------------------ auth


def test_api_key_protects_agent_routes_but_not_health():
    key = "k" * 40
    with make_client(Settings(environment="test", api_key=key)) as c:
        assert c.get("/health/live").status_code == 200
        missing = c.post(MESSAGE, json=request_body())
        assert_error(missing, 401, "invalid_request")
        assert missing.headers["www-authenticate"] == "Bearer"
        assert_error(c.post(MESSAGE, json=request_body(), headers={"Authorization": "Bearer wrong"}), 401, "invalid_request")
        ok = c.post(MESSAGE, json=request_body(), headers={"Authorization": f"Bearer {key}"})
        assert ok.status_code == 200 and key not in ok.text


# ------------------------------------------------------------------ CORS


def test_cors_allows_only_configured_origins():
    allowed = "http://localhost:5173"
    with make_client(Settings(environment="test", cors_origins=[allowed])) as c:
        preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
        ok = c.options(STREAM, headers={"Origin": allowed, **preflight})
        assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == allowed
        assert "access-control-allow-credentials" not in ok.headers
        denied = c.options(STREAM, headers={"Origin": "https://evil.example", **preflight})
        assert "access-control-allow-origin" not in denied.headers
        simple = c.get("/health/live", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in simple.headers


# ------------------------------------------------------------------ streaming


def stream(client, body) -> tuple[object, list]:
    with client.stream("POST", STREAM, json=body) as response:
        raw = "".join(response.iter_text())
    return response, parse_sse(raw)


@pytest.mark.parametrize("field, text", [("poltava", SCENARIO_C), ("kansas", SCENARIO_B), ("mykolaiv", "Що відомо?")])
def test_stream_events_are_valid_ordered_and_terminated(client, field, text):
    response, frames = stream(client, request_body(field, text))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    validator = schema_validator("AgentEvent")
    for name, _id, data in frames:
        validator.validate(data)               # public JSON Schema, not just Pydantic
        assert name == data["event"]           # SSE event name mirrors the contract's event field
    assert [int(i) for _n, i, _d in frames] == list(range(1, len(frames) + 1))
    names = [n for n, _i, _d in frames]
    assert names[0] == "turn.started" and names[-1] == "turn.completed"
    assert names.count("turn.completed") + names.count("error") == 1
    turn_id = frames[0][2]["turn_id"]
    assert frames[-1][2]["message"]["turn_id"] == turn_id
    # tool events come in started/finished pairs; deltas rebuild the final text
    started = [d["call_id"] for n, _i, d in frames if n == "tool.started"]
    finished = [d["call_id"] for n, _i, d in frames if n == "tool.finished"]
    assert started == finished
    deltas = "".join(d["text"] for n, _i, d in frames if n == "text.delta")
    assert deltas == frames[-1][2]["message"]["blocks"][0]["markdown"]


def test_stream_and_message_endpoints_agree(client):
    body = request_body("poltava", SCENARIO_C)
    _response, frames = stream(client, body)
    streamed = frames[-1][2]["message"]
    direct = client.post(MESSAGE, json=body).json()
    for message in (streamed, direct):
        message.pop("turn_id")
        message["meta"].pop("latency_ms")
    assert streamed == direct


def test_stream_reports_agent_failure_as_single_error_event():
    with make_client(agent=ExplodingAgent()) as c:
        response, frames = stream(c, request_body())
    assert response.status_code == 200  # the stream had started; the failure is an event
    names = [n for n, _i, _d in frames]
    assert names[0] == "turn.started" and names[-1] == "error" and names.count("error") == 1
    error = frames[-1][2]
    assert error["code"] == "internal" and "hunter2" not in json.dumps(frames)


def test_stream_errors_before_start_are_plain_http_errors(client):
    body = request_body()
    body["locale"] = "fr"
    response = client.post(STREAM, json=body)
    assert_error(response, 422, "invalid_request")
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.anyio
async def test_client_disconnect_cancels_agent_work():
    """Drive the ASGI app directly, the way uvicorn does (spec 2.3: server reports http.disconnect)."""
    agent = SlowAgent()
    app = create_app(Settings(environment="test"), lambda _s: agent)
    body = json.dumps(request_body()).encode()
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": STREAM, "raw_path": STREAM.encode(), "root_path": "",
        "query_string": b"", "server": ("test", 80), "client": ("127.0.0.1", 1234),
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
    }
    body_sent, disconnected = False, asyncio.Event()
    chunks: list[bytes] = []

    async def receive():
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))
            if b"text.delta" in message.get("body", b""):
                disconnected.set()  # the browser tab closes right after the first delta

    async with app.router.lifespan_context(app):
        await asyncio.wait_for(app(scope, receive, send), timeout=5)
    assert agent.closed
    assert b"turn.completed" not in b"".join(chunks)


@pytest.mark.anyio
@pytest.mark.parametrize("started, errors, swallowed", [
    (True, [anyio.BrokenResourceError()], True),             # client gone mid-stream: a normal disconnect
    (False, [anyio.BrokenResourceError()], False),           # before any response: a real failure
    (True, [anyio.BrokenResourceError(), ValueError()], False),
    (True, [ValueError()], False),
])
async def test_disconnect_filter_only_swallows_closed_stream_errors(started, errors, swallowed):
    from agent_service.api import RequestContextMiddleware

    async def app(scope, receive, send):
        if started:
            await send({"type": "http.response.start", "status": 200, "headers": []})
        raise ExceptionGroup("tg", errors)

    async def send(message):
        pass

    middleware = RequestContextMiddleware(app)
    scope = {"type": "http", "method": "POST", "path": "/v1/agent/stream", "headers": []}
    if swallowed:
        await middleware(scope, None, send)
    else:
        with pytest.raises(ExceptionGroup):
            await middleware(scope, None, send)


# ------------------------------------------------------------------ contract compatibility


def test_documented_example_request_matches_public_schema_and_works(client):
    example = json.loads((ROOT / "examples" / "agent_request.scenario_b.json").read_text(encoding="utf-8"))
    schema_validator("AgentRequest").validate(example)
    response = client.post(MESSAGE, json=example)
    assert response.status_code == 200
    assert {c["type"] for c in response.json()["constraints_proposed"]} == {"irrigation_available", "fertilizer_budget"}


def test_openapi_references_resolve(client):
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    refs = set()

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("$ref"), str):
                refs.add(node["$ref"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(spec)
    assert refs and all(r.startswith("#/components/schemas/") and r.rsplit("/", 1)[1] in schemas for r in refs)
    body = spec["paths"][MESSAGE]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert body == {"$ref": "#/components/schemas/AgentRequest"}


def test_production_hides_docs():
    settings = Settings(environment="production", api_key="p" * 40, cors_origins=["https://fieldshift.example"],
                        context_signing_key="s" * 40, require_context_attestation=True)
    with make_client(settings) as c:
        assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404


def test_request_body_is_not_mutated_between_requests(client):
    body = request_body("poltava", SCENARIO_B)
    snapshot = copy.deepcopy(body)
    client.post(MESSAGE, json=body)
    assert body == snapshot
    assert context("poltava") == snapshot["field_context"]
