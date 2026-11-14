"""Exercise the installed SDK and agent loop against local HTTP fixtures."""

import json

import httpx2
import openai
import pytest

from agent_service.llm.openai_provider import OpenAIProvider
from agent_service.llm.provider import (
    AssistantTurn,
    ProviderError,
    ToolResult,
    ToolResults,
    ToolSpec,
    Usage,
    UserText,
)
from agent_service.llm_agent import LLMAgent

from .conftest import make_client, request_body, schema_validator

BASE_URL = "https://gateway.example.test/team/v1"
KEY = "local-mock-key"
MODEL = "gpt-luna"
SPECS = [ToolSpec("get_field_facts", "Read the selected field", {"type": "object"})]


def reply(*, finish="stop", message=None, **extra):
    return {
        "id": "chatcmpl-local",
        "object": "chat.completion",
        "created": 1770000000,
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "finish_reason": finish,
                "message": message
                or {
                    "role": "assistant",
                    "content": "Готово.",
                },
            }
        ],
        **extra,
    }


def function(call_id, name, arguments):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def provider(
    responses,
    captured,
    *,
    token_limit_field="max_completion_tokens",
    model=MODEL,
    reasoning_effort=None,
):
    async def handler(request):
        captured.append((request, json.loads(request.content)))
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item
        return httpx2.Response(status, json=body)

    http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    sdk = openai.AsyncOpenAI(
        api_key=KEY, base_url=BASE_URL, http_client=http_client, max_retries=0
    )
    return OpenAIProvider(
        api_key=KEY,
        base_url=BASE_URL,
        model_id=model,
        request_timeout_s=10,
        max_retries=0,
        token_limit_field=token_limit_field,
        reasoning_effort=reasoning_effort,
        client=sdk,
    )


async def complete(p, transcript=None, tools=SPECS):
    return await p.complete(
        system="SYSTEM",
        transcript=transcript or [UserText("Покажи поле")],
        tools=tools,
        max_output_tokens=1024,
    )


@pytest.mark.anyio
async def test_gateway_alias_and_actual_tool_call_round_trip():
    captured = []
    calls = [
        function("call_1", "get_field_facts", '{ "domains": ["water"] }'),
        function("call_2", "get_field_facts", "not json"),
    ]
    p = provider(
        [
            (
                200,
                reply(
                    finish="tool_calls",
                    message={
                        "role": "assistant",
                        "content": None,
                        "reasoning_content": "private round metadata",
                        "tool_calls": calls,
                    },
                ),
            ),
            (
                200,
                reply(
                    usage={
                        "prompt_tokens": 130,
                        "completion_tokens": 20,
                        "total_tokens": 150,
                        "prompt_tokens_details": {"cached_tokens": 12},
                        "completion_tokens_details": {"reasoning_tokens": 6},
                    }
                ),
            ),
        ],
        captured,
    )
    try:
        first = await complete(p)
        assert first.stop_reason == "tool_use"
        assert first.text == ""
        assert [(c.id, c.name, c.arguments) for c in first.tool_calls] == [
            ("call_1", "get_field_facts", {"domains": ["water"]}),
            ("call_2", "get_field_facts", None),
        ]
        results = (
            ToolResult("call_1", '{"facts":[]}'),
            ToolResult("call_2", '{"error":"invalid"}', True),
        )
        second = await complete(
            p, [UserText("Покажи поле"), AssistantTurn(first), ToolResults(results)]
        )
        assert (second.stop_reason, second.text, second.model_id) == (
            "end_turn",
            "Готово.",
            MODEL,
        )
        assert second.usage == Usage(
            input_tokens=130, output_tokens=20, cache_read_input_tokens=12
        )
        request, body = captured[0]
        assert str(request.url) == f"{BASE_URL}/chat/completions"
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        assert body["model"] == MODEL and body["max_completion_tokens"] == 1024
        assert "reasoning_effort" not in body
        assert "max_tokens" not in body and "parallel_tool_calls" not in body
        assert body["messages"] == [
            {"role": "system", "content": "SYSTEM"},
            {"role": "user", "content": "Покажи поле"},
        ]
        assert body["tools"][0]["function"]["parameters"] == {"type": "object"}
        assistant, result_one, result_two = captured[1][1]["messages"][-3:]
        assert assistant["tool_calls"] == calls and assistant["content"] is None
        assert assistant["reasoning_content"] == "private round metadata"
        assert result_one == {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": '{"facts":[]}',
        }
        assert result_two == {
            "role": "tool",
            "tool_call_id": "call_2",
            "content": '{"error":"invalid"}',
        }
    finally:
        await p.aclose()


@pytest.mark.anyio
async def test_luna_tool_request_uses_explicit_none_reasoning_effort():
    captured = []
    p = provider(
        [(200, reply(model="gpt-6-luna"))],
        captured,
        model="gpt-6-luna",
        reasoning_effort="none",
    )
    try:
        result = await complete(p)
        body = captured[0][1]
        assert result.model_id == body["model"] == "gpt-6-luna"
        assert body["reasoning_effort"] == "none"
        assert body["tools"][0]["function"]["name"] == "get_field_facts"
    finally:
        await p.aclose()


@pytest.mark.anyio
async def test_legacy_token_option_and_plain_request():
    captured = []
    p = provider([(200, reply())], captured, token_limit_field="max_tokens")
    try:
        result = await complete(p, tools=[])
        body = captured[0][1]
        assert result.stop_reason == "end_turn"
        assert body["max_tokens"] == 1024
        assert "max_completion_tokens" not in body and "tools" not in body
    finally:
        await p.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "finish,message,expected",
    [
        ("length", {"role": "assistant", "content": "Unfinished"}, "max_tokens"),
        ("content_filter", {"role": "assistant", "content": None}, "refusal"),
        ("stop", {"role": "assistant", "refusal": "Refusal details"}, "refusal"),
        (
            "tool_calls",
            {"role": "assistant", "content": None, "tool_calls": []},
            "unexpected",
        ),
        (
            "tool_calls",
            {
                "role": "assistant",
                "tool_calls": [
                    function("dup", "get_field_facts", "{}"),
                    function("dup", "get_field_facts", "{}"),
                ],
            },
            "unexpected",
        ),
    ],
)
async def test_incomplete_refused_or_ambiguous_turns_never_execute_calls(
    finish, message, expected
):
    p = provider([(200, reply(finish=finish, message=message))], [])
    try:
        result = await complete(p)
        assert result.stop_reason == expected and not result.tool_calls
        assert "Refusal details" not in (result.refusal_category or "")
    finally:
        await p.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status,code,kind,retryable",
    [
        (400, "context_length_exceeded", "context_exceeded", False),
        (401, None, "auth", False),
        (408, None, "timeout", True),
        (429, None, "rate_limited", True),
        (502, None, "unavailable", True),
        (400, None, "bad_request", False),
    ],
)
async def test_http_errors_do_not_expose_gateway_error_text(
    status, code, kind, retryable
):
    secret = "provider body can contain confidential request data"
    p = provider([(status, {"error": {"message": secret, "code": code}})], [])
    try:
        with pytest.raises(ProviderError) as caught:
            await complete(p)
        assert (caught.value.kind, caught.value.retryable) == (kind, retryable)
        assert secret not in str(caught.value) and caught.value.__suppress_context__
    finally:
        await p.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "transport_error,kind",
    [
        (httpx2.ReadTimeout("private URL"), "timeout"),
        (httpx2.ConnectError("private URL"), "unavailable"),
    ],
)
async def test_transport_errors_have_safe_details(transport_error, kind):
    p = provider([transport_error], [])
    try:
        with pytest.raises(ProviderError) as caught:
            await complete(p)
        assert caught.value.kind == kind and caught.value.retryable
        assert "private URL" not in str(caught.value)
    finally:
        await p.aclose()


@pytest.mark.anyio
async def test_configured_provider_does_not_follow_gateway_redirects(monkeypatch):
    urls = []

    async def handler(request):
        urls.append(str(request.url))
        return httpx2.Response(
            307, headers={"location": "https://unexpected.example.test/target"}
        )

    factory = openai.DefaultAsyncHttpx2Client

    def mock_client(**kwargs):
        return factory(transport=httpx2.MockTransport(handler), **kwargs)

    monkeypatch.setattr(openai, "DefaultAsyncHttpx2Client", mock_client)
    p = OpenAIProvider(
        api_key=KEY,
        base_url=BASE_URL,
        model_id=MODEL,
        request_timeout_s=10,
        max_retries=0,
    )
    try:
        with pytest.raises(ProviderError, match="HTTP 307"):
            await complete(p)
        assert urls == [f"{BASE_URL}/chat/completions"]
    finally:
        await p.aclose()


@pytest.mark.anyio
async def test_configured_gateway_uses_only_its_configured_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-test-key")
    monkeypatch.setenv("OPENAI_ORG_ID", "ambient-test-organization")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "ambient-test-project")
    monkeypatch.delenv("OPENAI_CUSTOM_HEADERS", raising=False)
    headers = []

    async def handler(request):
        headers.append(request.headers)
        return httpx2.Response(200, json=reply())

    factory = openai.DefaultAsyncHttpx2Client

    def mock_client(**kwargs):
        return factory(transport=httpx2.MockTransport(handler), **kwargs)

    monkeypatch.setattr(openai, "DefaultAsyncHttpx2Client", mock_client)
    p = OpenAIProvider(
        api_key=KEY,
        base_url=BASE_URL,
        model_id=MODEL,
        request_timeout_s=10,
        max_retries=0,
    )
    try:
        assert (await complete(p)).stop_reason == "end_turn"
        assert len(headers) == 1 and headers[0]["Authorization"] == f"Bearer {KEY}"
        assert (
            "OpenAI-Organization" not in headers[0]
            and "OpenAI-Project" not in headers[0]
        )
    finally:
        await p.aclose()


def test_ambient_custom_headers_are_rejected_before_constructing_the_sdk(monkeypatch):
    ambient = (
        "Authorization: Bearer ambient-test-key\nX-Private-Token: ambient-test-value"
    )
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", ambient)

    def sdk_must_not_be_constructed(**kwargs):
        pytest.fail("The SDK must not ingest ambient custom headers")

    monkeypatch.setattr(openai, "AsyncOpenAI", sdk_must_not_be_constructed)
    with pytest.raises(ValueError, match="OPENAI_CUSTOM_HEADERS") as caught:
        OpenAIProvider(
            api_key=KEY,
            base_url=BASE_URL,
            model_id=MODEL,
            request_timeout_s=10,
            max_retries=0,
        )
    assert "ambient-test" not in str(caught.value)


def test_agent_applies_local_tool_policy_and_grounding_with_openai_protocol():
    captured = []
    p = provider(
        [
            (
                200,
                reply(
                    finish="tool_calls",
                    message={
                        "role": "assistant",
                        "tool_calls": [
                            function("call_forbidden", "delete_field", "{}"),
                            function(
                                "call_facts",
                                "get_field_facts",
                                '{"domains":["water"],"fact_ids":[]}',
                            ),
                        ],
                    },
                ),
            ),
            (
                200,
                reply(
                    message={
                        "role": "assistant",
                        "content": "Вологість кореневої зони: {{fact:f_sm_rootzone_2025_07}}.",
                    }
                ),
            ),
        ],
        captured,
    )
    with make_client(agent=LLMAgent(p)) as client:
        response = client.post(
            "/v1/agent/message", json=request_body("poltava", "Яка вологість на полі?")
        )
    assert response.status_code == 200, response.text
    data = response.json()
    schema_validator("AgentMessage").validate(data)
    assert data["meta"]["provider"] == "openai" and data["meta"]["model_id"] == MODEL
    assert (
        "0,21" in data["blocks"][0]["markdown"]
        and "{{" not in data["blocks"][0]["markdown"]
    )
    assert "fixture_data" in {warning["code"] for warning in data["warnings"]}
    result = captured[1][1]["messages"][-2]
    assert result["tool_call_id"] == "call_forbidden"
    assert json.loads(result["content"])["error"]["category"] == "unknown_tool"
