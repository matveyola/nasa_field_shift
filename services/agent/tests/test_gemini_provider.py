"""The Gemini adapter through the real google-genai SDK, with HTTP served by a mock transport (no network, no key).

Responses are shaped like the documented generateContent JSON; what the SDK actually sends is asserted from the
captured HTTP bodies (thought signatures, function-call ids, config), so these tests cover the wire format too.
"""

import asyncio
import json
import logging

import httpx2
import pytest
from google import genai

from agent_service.config import Settings
from agent_service.llm.gemini_provider import GeminiProvider, GeminiUnavailable, declaration, http_options
from agent_service.llm.provider import AssistantTurn, ProviderError, ToolResult, ToolResults, Usage, UserText
from agent_service.llm_agent import LLMAgent
from agent_service.tools import default_registry

from .conftest import make_client, request_body, schema_validator

SPECS = [t.spec for t in default_registry().values()]
FAKE_KEY = "gemini-test-key-not-real"
SIG = "c2lnLWFsbG93ZWQtYnl0ZXM="  # base64 of an opaque thought signature


def reply(parts, finish="STOP", usage=None, **extra):
    return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}],
            "usageMetadata": usage or {"promptTokenCount": 1200, "candidatesTokenCount": 80, "thoughtsTokenCount": 150,
                                       "totalTokenCount": 1430},
            "modelVersion": "gemini-3.8-flash", **extra}


def fcall(name, args, call_id, signature=None):
    part = {"functionCall": {"id": call_id, "name": name, "args": args}}
    return {**part, "thoughtSignature": signature} if signature else part


def provider(responses, captured, *, max_retries=0, thinking_level="low"):
    queue = list(responses)

    async def handler(request):
        captured.append(json.loads(request.content))
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return await item()
        status, body = item
        return httpx2.Response(status, json=body)

    options = http_options(10, max_retries)
    options.httpx_async_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    client = genai.Client(api_key=FAKE_KEY, http_options=options)
    return GeminiProvider(model_id="gemini-3.8-flash", thinking_level=thinking_level, request_timeout_s=10,
                          max_retries=max_retries, client=client)


async def complete(p, transcript=None, tools=SPECS):
    return await p.complete(system="SYSTEM", transcript=transcript or [UserText("Що відомо про поле?")],
                            tools=tools, max_output_tokens=2048)


# ------------------------------------------------------------------ request shape and plain answers


@pytest.mark.anyio
async def test_plain_ukrainian_answer_and_documented_request_shape():
    captured = []
    p = provider([(200, reply([{"text": "думаю…", "thought": True}, {"text": "Ґрунт на полі — суглинок."}]))], captured)
    r = await complete(p)
    assert (r.stop_reason, r.text, r.tool_calls, r.model_id) == ("end_turn", "Ґрунт на полі — суглинок.", (), "gemini-3.8-flash")
    body = captured[0]
    config = body["generationConfig"]
    assert config["maxOutputTokens"] == 2048 and json.dumps(config["thinkingConfig"]).count("LOW") == 1
    # Deprecated / unsupported for Gemini 3.x, and nothing Anthropic-specific
    for absent in ("temperature", "topP", "topK", "candidateCount", "effort", "output_config", "thinkingBudget"):
        assert absent not in json.dumps(config)
    assert body["systemInstruction"]["parts"][0]["text"] == "SYSTEM"
    assert body["toolConfig"]["functionCallingConfig"]["mode"] == "AUTO"
    assert [list(t) for t in body["tools"]] == [["functionDeclarations"]]  # no Google Search (billed) or other tools
    names = [d["name"] for d in body["tools"][0]["functionDeclarations"]]
    assert names == [s.name for s in SPECS]
    assert '"const"' not in json.dumps(body["tools"])  # written as one-value enums


def test_schema_translation_keeps_meaning():
    for spec in SPECS:
        schema = declaration(spec).parameters_json_schema
        before, after = json.dumps(spec.input_schema), json.dumps(schema)
        assert schema["type"] == "object" and '"const":' not in after
        assert after.count('"enum":') == before.count('"enum":') + before.count('"const":')  # each const -> enum


@pytest.mark.parametrize("level", ["minimal", "none", "max"])
def test_only_documented_thinking_levels(level):
    with pytest.raises(ValueError):
        provider([], [], thinking_level=level)
    with pytest.raises(Exception):
        Settings(agent="gemini", gemini_thinking_level=level)


# ------------------------------------------------------------------ function calling


@pytest.mark.anyio
async def test_tool_round_trip_replays_signatures_and_links_responses_by_id():
    captured = []
    p = provider([
        (200, reply([fcall("get_field_facts", {"domains": ["soil"]}, "fc_a", SIG),
                     fcall("search_knowledge", {"query": "водний стрес"}, "fc_b")])),
        (200, reply([{"text": "Готово.", "thoughtSignature": SIG}])),
    ], captured)
    first = await complete(p)
    assert first.stop_reason == "tool_use"
    assert [(c.id, c.name, c.arguments) for c in first.tool_calls] == [
        ("fc_a", "get_field_facts", {"domains": ["soil"]}), ("fc_b", "search_knowledge", {"query": "водний стрес"})]
    transcript = [UserText("Що відомо про поле?"), AssistantTurn(first),
                  ToolResults((ToolResult("fc_a", '{"facts": []}'), ToolResult("fc_b", '{"error": {"category": "x"}}', True)))]
    second = await complete(p, transcript)
    assert second.stop_reason == "end_turn" and second.text == "Готово."
    model_turn, results = captured[1]["contents"][1], captured[1]["contents"][2]
    assert model_turn == {"role": "model", "parts": captured_parts(first)}  # replayed unchanged
    assert model_turn["parts"][0]["thoughtSignature"] == SIG
    # Role "user": the live API rejects "tool" with 400 INVALID_ARGUMENT.
    assert results["role"] == "user" and [r["functionResponse"]["id"] for r in results["parts"]] == ["fc_a", "fc_b"]
    assert [r["functionResponse"]["name"] for r in results["parts"]] == ["get_field_facts", "search_knowledge"]
    assert results["parts"][1]["functionResponse"]["response"] == {"error": {"category": "x"}}


def captured_parts(response):
    return [fcall("get_field_facts", {"domains": ["soil"]}, "fc_a", SIG),
            fcall("search_knowledge", {"query": "водний стрес"}, "fc_b")] if response.tool_calls else []


@pytest.mark.anyio
async def test_calls_without_ids_get_local_ids_and_none_is_echoed():
    captured = []
    p = provider([(200, reply([{"functionCall": {"name": "get_field_facts", "args": {}}}])), (200, reply([{"text": "ok"}]))],
                 captured)
    first = await complete(p)
    (call,) = first.tool_calls
    assert call.id.startswith("gemini_call_") and call.arguments == {}
    await complete(p, [UserText("q"), AssistantTurn(first), ToolResults((ToolResult(call.id, '{"facts": []}'),))])
    response = captured[1]["contents"][2]["parts"][0]["functionResponse"]
    assert "id" not in response and response["name"] == "get_field_facts"


# ------------------------------------------------------------------ stop reasons and errors


@pytest.mark.anyio
@pytest.mark.parametrize("finish, normal", [("MAX_TOKENS", "max_tokens"), ("SAFETY", "refusal"),
                                            ("PROHIBITED_CONTENT", "refusal"), ("RECITATION", "refusal"),
                                            ("MALFORMED_FUNCTION_CALL", "unexpected"), ("OTHER", "unexpected")])
async def test_finish_reasons_are_normalised(finish, normal):
    r = await complete(provider([(200, reply([{"text": "частково"}], finish=finish))], []))
    assert (r.stop_reason, r.raw_stop_reason) == (normal, finish)


@pytest.mark.anyio
async def test_blocked_prompt_is_a_refusal():
    body = {"promptFeedback": {"blockReason": "SAFETY"}, "usageMetadata": {"promptTokenCount": 50}, "modelVersion": "gemini-3.8-flash"}
    r = await complete(provider([(200, body)], []))
    assert (r.stop_reason, r.refusal_category, r.usage.input_tokens) == ("refusal", "SAFETY", 50)




def error(code, status, message="farmer text must never be logged"):
    return code, {"error": {"code": code, "message": message, "status": status}}


@pytest.mark.anyio
@pytest.mark.parametrize("response, kind, retryable", [
    (error(429, "RESOURCE_EXHAUSTED"), "rate_limited", True),
    (error(401, "UNAUTHENTICATED"), "auth", False),
    (error(403, "PERMISSION_DENIED"), "auth", False),
    (error(503, "UNAVAILABLE"), "unavailable", True),
    (error(504, "DEADLINE_EXCEEDED"), "timeout", True),
    (error(400, "INVALID_ARGUMENT", "The input token count exceeds the maximum number of tokens allowed."),
     "context_exceeded", False),
    (error(400, "INVALID_ARGUMENT"), "bad_request", False),
])
async def test_http_errors_map_to_provider_errors_without_leaking(response, kind, retryable, caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ProviderError) as e:
        await complete(provider([response], []))
    assert (e.value.kind, e.value.retryable) == (kind, retryable)
    assert "farmer text" not in str(e.value) and FAKE_KEY not in caplog.text


@pytest.mark.anyio
async def test_rate_limit_is_retried_within_the_configured_budget():
    captured = []
    p = provider([error(429, "RESOURCE_EXHAUSTED"), (200, reply([{"text": "ok"}]))], captured, max_retries=1)
    r = await complete(p)
    assert r.text == "ok" and len(captured) == 2


@pytest.mark.anyio
async def test_timeout_and_connection_errors():
    with pytest.raises(ProviderError) as e:
        await complete(provider([httpx2.ReadTimeout("slow")], []))
    assert e.value.kind == "timeout"
    with pytest.raises(ProviderError) as e:
        await complete(provider([httpx2.ConnectError("down")], []))
    assert e.value.kind == "unavailable"


@pytest.mark.anyio
async def test_cancellation_aborts_the_request():
    started = asyncio.Event()

    async def hang():
        started.set()
        await asyncio.sleep(3600)

    task = asyncio.create_task(complete(provider([hang], [])))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_usage_separates_input_output_and_thinking():
    r = await complete(provider([(200, reply([{"text": "ok"}], usage={
        "promptTokenCount": 2000, "candidatesTokenCount": 100, "thoughtsTokenCount": 300, "cachedContentTokenCount": 500,
        "totalTokenCount": 2400})), ], []))
    assert r.usage == Usage(input_tokens=2000, output_tokens=100, cache_read_input_tokens=500, thinking_tokens=300)
    # published intro price 0.75 / 3.75 USD per 1M tokens; thinking billed as output
    assert r.usage.cost_usd(0.75, 3.75) == pytest.approx((2000 * 0.75 + 400 * 3.75) / 1e6)
    # the 500 cached input tokens at the cached price (a tenth of input on 2026-11-12): input is never counted twice
    assert r.usage.cost_usd(0.75, 3.75, 0.075) == pytest.approx((1500 * 0.75 + 500 * 0.075 + 400 * 3.75) / 1e6)


# ------------------------------------------------------------------ through the agent loop and the HTTP API


def test_agent_turn_with_gemini_validates_tools_and_grounds_the_answer():
    captured = []
    p = provider([
        # an invalid argument and a tool that does not exist: both rejected by the server, never executed
        (200, reply([fcall("get_field_facts", {"domains": "water"}, "fc_1", SIG), fcall("delete_field", {}, "fc_2")])),
        (200, reply([fcall("get_field_facts", {"domains": ["water"], "fact_ids": []}, "fc_3", SIG)])),
        (200, reply([{"text": "Вологість кореневої зони: {{fact:f_sm_rootzone_2025_07}}.", "thoughtSignature": SIG}])),
    ], captured)
    with make_client(agent=LLMAgent(p)) as c:
        data = c.post("/v1/agent/message", json=request_body("poltava", "Яка вологість на полі?")).json()
    schema_validator("AgentMessage").validate(data)
    assert data["meta"]["provider"] == "gemini" and data["meta"]["model_id"] == "gemini-3.8-flash"
    assert "0,21" in data["blocks"][0]["markdown"] and "{{" not in data["blocks"][0]["markdown"]
    assert {w["code"] for w in data["warnings"]} >= {"fixture_data"}  # synthetic stays labelled
    rejected = {r["functionResponse"]["id"]: r["functionResponse"]["response"] for r in captured[1]["contents"][2]["parts"]}
    assert rejected["fc_1"]["error"]["category"] == "invalid_arguments"
    assert rejected["fc_2"]["error"]["category"] == "unknown_tool"
    assert captured[2]["contents"][3]["parts"][0]["thoughtSignature"] == SIG  # continuity across every round


def test_truncated_answer_is_never_shown():
    p = provider([(200, reply([{"text": "Вологість кореневої зони становить"}], finish="MAX_TOKENS"))], [])
    with make_client(agent=LLMAgent(p)) as c:
        response = c.post("/v1/agent/message", json=request_body("poltava", "Яка вологість?"))
    assert response.status_code == 502 and response.json()["error"]["message_key"] == "error.llm_incomplete"


def test_refusal_returns_the_canned_answer_and_proposes_nothing():
    p = provider([(200, reply([], finish="SAFETY"))], [])
    with make_client(agent=LLMAgent(p)) as c:
        data = c.post("/v1/agent/message", json=request_body("poltava", "Розкажи щось недозволене.")).json()
    assert data["blocks"][0]["markdown"].startswith("Вибачте") and not data["ui_actions"] and not data["constraints_proposed"]


# ------------------------------------------------------------------ configuration and missing credentials


def test_gemini_settings():
    s = Settings.from_env({"AGENT_AGENT": "gemini", "AGENT_GEMINI_MODEL": "gemini-3.8-flash",
                           "AGENT_GEMINI_THINKING_LEVEL": "medium", "AGENT_LLM_PRICE_INPUT_USD_PER_MTOK": "0.75",
                           "AGENT_LLM_PRICE_OUTPUT_USD_PER_MTOK": "3.75"})
    assert (s.agent, s.gemini_model, s.gemini_thinking_level) == ("gemini", "gemini-3.8-flash", "medium")
    assert Settings(agent="gemini").gemini_thinking_level == "low"  # economical default
    assert "GEMINI_API_KEY" not in Settings.model_fields and "gemini_api_key" not in Settings.model_fields


def test_gemini_agent_builds_without_network(monkeypatch):
    from agent_service.agent import build_agent

    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    agent = build_agent(Settings(agent="gemini"))
    assert agent.name == "llm/gemini" and agent.provider.model_id == "gemini-3.8-flash"
    assert agent.provider.thinking_level == "low"


def test_missing_key_reports_gemini_unavailable_and_never_falls_back(monkeypatch):
    import agent_service.llm.anthropic_provider as anthropic_provider
    from agent_service.agent import build_agent

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")  # even with Claude credentials present

    def no_claude(*a, **k):
        raise AssertionError("Gemini must never fall back to Claude")

    monkeypatch.setattr(anthropic_provider, "AnthropicProvider", no_claude)
    with pytest.raises(GeminiUnavailable):
        build_agent(Settings(agent="gemini"))
    with make_client(Settings(environment="test", agent="gemini")) as c:
        ready = c.get("/health/ready")
        assert ready.status_code == 503 and ready.json() == {"status": "not_ready", "reason": "llm_credentials_missing"}
        turn = c.post("/v1/agent/message", json=request_body())
        assert turn.status_code == 503  # no answer at all, rather than one from another model


def test_scripted_mode_is_never_labelled_as_gemini():
    with make_client(Settings(environment="test", agent="scripted-llm")) as c:
        ready = c.get("/health/ready").json()
        message = c.post("/v1/agent/message", json=request_body()).json()
    assert ready["agent"] == "llm/scripted-llm" and "model" not in ready
    assert message["meta"]["provider"] == "scripted-llm" and "gemini" not in json.dumps(message["meta"])
