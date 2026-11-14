"""The Anthropic adapter through the real SDK, with HTTP served by a mock transport (no network, no key)."""

import json
import logging

import anthropic
import httpx2
import pytest

from agent_service.llm.anthropic_provider import FALLBACK_BETA, AnthropicProvider
from agent_service.llm.provider import AssistantTurn, ProviderError, ToolResult, ToolResults, UserText
from agent_service.tools import default_registry

FAKE_KEY = "sk-ant-test-0000000000000000000000000000000000"
SPECS = [t.spec for t in default_registry().values()]


def message(content, stop_reason="end_turn", **extra):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": content,
            "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 7,
                      "cache_creation_input_tokens": 0}, **extra}


def provider(responses, captured, **kwargs):
    queue = list(responses)

    def handler(request):
        captured.append(request)
        status, body = queue.pop(0)
        return httpx2.Response(status, json=body)

    client = anthropic.AsyncAnthropic(api_key=FAKE_KEY, max_retries=0,
                                      http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)))
    options = dict(model_id="claude-opus-5-5", effort="medium", request_timeout_s=10, max_retries=0,
                   refusal_fallback=True, strict_tools=True)
    options.update(kwargs)
    return AnthropicProvider(client=client, **options)


@pytest.mark.anyio
async def test_request_shape_follows_the_documented_api():
    sent = []
    p = provider([(200, message([{"type": "text", "text": "ok"}]))], sent)
    response = await p.complete(system="RULES", transcript=[UserText("hi")], tools=SPECS, max_output_tokens=1024)
    body = json.loads(sent[0].content)
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 1024
    assert body["output_config"] == {"effort": "medium"}
    assert "tool_choice" not in body          # Opus 5.5 rejects forced tool choice; auto is the default
    assert "thinking" not in body             # adaptive thinking is always on; disabling it is a 400
    assert not {"temperature", "top_p", "top_k"} & set(body)
    assert body["fallbacks"] == "default" and sent[0].headers["anthropic-beta"] == FALLBACK_BETA
    assert body["system"] == [{"type": "text", "text": "RULES", "cache_control": {"type": "ephemeral"}}]
    assert all(t["strict"] is True and t["input_schema"]["additionalProperties"] is False for t in body["tools"])
    assert response.stop_reason == "end_turn" and response.text == "ok" and response.usage.cache_read_input_tokens == 7
    assert response.usage.input_tokens == 107  # Usage counts every input token, cached or not (as Gemini reports it)


@pytest.mark.anyio
async def test_fallback_can_be_disabled():
    sent = []
    p = provider([(200, message([{"type": "text", "text": "ok"}]))], sent, refusal_fallback=False, strict_tools=False)
    await p.complete(system="S", transcript=[UserText("hi")], tools=SPECS, max_output_tokens=256)
    body = json.loads(sent[0].content)
    assert "fallbacks" not in body and "anthropic-beta" not in sent[0].headers
    assert all("strict" not in t for t in body["tools"])


@pytest.mark.anyio
async def test_tool_round_trip_replays_thinking_unchanged_and_groups_results():
    sent = []
    first = message([{"type": "thinking", "thinking": "", "signature": "sig-abc"},
                     {"type": "tool_use", "id": "toolu_1", "name": "get_field_facts", "input": {"domains": [], "fact_ids": []}},
                     {"type": "tool_use", "id": "toolu_2", "name": "propose_ui_actions", "input": {"actions": []}}],
                    stop_reason="tool_use")
    p = provider([(200, first), (200, message([{"type": "text", "text": "done"}]))], sent)
    r1 = await p.complete(system="S", transcript=[UserText("hi")], tools=SPECS, max_output_tokens=256)
    assert [c.id for c in r1.tool_calls] == ["toolu_1", "toolu_2"] and r1.stop_reason == "tool_use"
    await p.complete(system="S", transcript=[UserText("hi"), AssistantTurn(r1), ToolResults((
        ToolResult("toolu_1", "{}"), ToolResult("toolu_2", '{"error": 1}', is_error=True)))],
        tools=SPECS, max_output_tokens=256)
    messages = json.loads(sent[1].content)["messages"]
    assert messages[1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig-abc"}
    assert [b["type"] for b in messages[2]["content"]] == ["tool_result", "tool_result"]
    assert messages[2]["content"][1]["is_error"] is True


@pytest.mark.anyio
async def test_fallback_boundary_is_respected():
    sent = []
    content = [{"type": "thinking", "thinking": "", "signature": "s1"},
               {"type": "tool_use", "id": "toolu_old", "name": "get_field_facts", "input": {}},
               {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-4-8"}},
               {"type": "tool_use", "id": "toolu_new", "name": "get_field_facts", "input": {"domains": [], "fact_ids": []}}]
    body = message(content, stop_reason="tool_use")
    body["usage"]["iterations"] = [{"type": "fallback_message", "input_tokens": 1, "output_tokens": 1}]
    p = provider([(200, body), (200, message([{"type": "text", "text": "ok"}]))], sent)
    r = await p.complete(system="S", transcript=[UserText("hi")], tools=SPECS, max_output_tokens=256)
    assert [c.id for c in r.tool_calls] == ["toolu_new"]       # only the serving model's calls run
    await p.complete(system="S", transcript=[UserText("hi"), AssistantTurn(r), ToolResults((ToolResult("toolu_new", "{}"),))],
                     tools=SPECS, max_output_tokens=256)
    replayed = json.loads(sent[1].content)["messages"][1]["content"]
    assert [b["type"] for b in replayed] == ["fallback", "tool_use"] and replayed[1]["id"] == "toolu_new"


@pytest.mark.anyio
@pytest.mark.parametrize("raw, normal", [("max_tokens", "max_tokens"), ("refusal", "refusal"),
                                         ("model_context_window_exceeded", "context_exceeded"),
                                         ("pause_turn", "unexpected"), ("stop_sequence", "unexpected")])
async def test_stop_reasons_are_normalised(raw, normal):
    extra = {"stop_details": {"type": "refusal", "category": "cyber", "explanation": None}} if raw == "refusal" else {}
    p = provider([(200, message([{"type": "text", "text": "x"}], stop_reason=raw, **extra))], [])
    r = await p.complete(system="S", transcript=[UserText("hi")], tools=SPECS, max_output_tokens=256)
    assert r.stop_reason == normal and r.raw_stop_reason == raw
    assert r.refusal_category == ("cyber" if raw == "refusal" else None)


@pytest.mark.anyio
@pytest.mark.parametrize("status, error_type, kind, retryable", [
    (429, "rate_limit_error", "rate_limited", True), (529, "overloaded_error", "unavailable", True),
    (500, "api_error", "unavailable", True), (401, "authentication_error", "auth", False),
    (400, "invalid_request_error", "bad_request", False), (404, "not_found_error", "bad_request", False),
])
async def test_http_errors_map_to_provider_errors_without_leaking(status, error_type, kind, retryable, caplog):
    body = {"type": "error", "error": {"type": error_type, "message": f"echo {FAKE_KEY}"}}
    p = provider([(status, body)], [])
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ProviderError) as info:
        await p.complete(system="S", transcript=[UserText("hi")], tools=SPECS, max_output_tokens=256)
    assert info.value.kind == kind and info.value.retryable is retryable
    assert FAKE_KEY not in str(info.value) and FAKE_KEY not in caplog.text


def test_tool_schemas_are_strict_compatible():
    """Strict tool use: every object closes additionalProperties; no unsupported keywords."""
    unsupported = {"minLength", "maxLength", "minimum", "maximum", "multipleOf", "pattern", "minItems", "maxItems"}

    def walk(node):
        if isinstance(node, dict):
            assert not unsupported & set(node), node
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False and set(node["required"]) == set(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for spec in SPECS:
        walk(spec.input_schema)
