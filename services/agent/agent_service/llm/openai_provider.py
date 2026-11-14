"""OpenAI-compatible Chat Completions via the official async Python SDK."""

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import openai
from openai.types.chat import ChatCompletion
from openai.types.shared import ReasoningEffort

from .provider import (
    AssistantTurn,
    Capabilities,
    ModelResponse,
    ProviderError,
    StopReason,
    ToolCall,
    ToolResults,
    ToolSpec,
    TranscriptItem,
    Usage,
    UserText,
)

TokenLimitField = Literal["max_completion_tokens", "max_tokens"]
_FINISH_REASONS: dict[str, StopReason] = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


class OpenAIProvider:
    name = "openai"
    # An endpoint can return multiple calls. The local loop validates and executes each one.
    capabilities = Capabilities(
        parallel_tool_calls=True, strict_tool_schemas=False, forced_tool_choice=False
    )

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model_id: str,
        request_timeout_s: float,
        max_retries: int,
        token_limit_field: TokenLimitField = "max_completion_tokens",
        reasoning_effort: ReasoningEffort = None,
        client: openai.AsyncOpenAI | None = None,
    ):
        if token_limit_field not in ("max_completion_tokens", "max_tokens"):
            raise ValueError("Unsupported OpenAI token limit field.")
        if client is None:
            # The SDK reads ambient headers even when HTTPX trust_env is disabled.
            if os.environ.get("OPENAI_CUSTOM_HEADERS", "").strip():
                raise ValueError(
                    "OPENAI_CUSTOM_HEADERS is unsupported by the agent. Use OPENAI_API_KEY instead."
                )
            client = openai.AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=request_timeout_s,
                max_retries=max_retries,
                default_headers={
                    "OpenAI-Organization": openai.omit,
                    "OpenAI-Project": openai.omit,
                },
                http_client=openai.DefaultAsyncHttpx2Client(
                    follow_redirects=False, trust_env=False
                ),
            )
        self.client = client
        self.model_id = model_id
        self.token_limit_field = token_limit_field
        self.reasoning_effort = reasoning_effort

    async def aclose(self) -> None:
        await self.client.close()

    async def complete(
        self,
        *,
        system: str,
        transcript: Sequence[TranscriptItem],
        tools: Sequence[ToolSpec],
        max_output_tokens: int,
    ) -> ModelResponse:
        params: dict[str, Any] = {
            "model": self.model_id,
            "messages": _messages(system, transcript),
            self.token_limit_field: max_output_tokens,
        }
        if self.reasoning_effort is not None:
            params["reasoning_effort"] = self.reasoning_effort
        if tools:
            params["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in tools
            ]
        try:
            completion = await self.client.chat.completions.create(**params)
        except openai.APIStatusError as error:
            raise _status_error(error) from None
        except openai.APITimeoutError:
            raise ProviderError(
                "timeout", "request timed out", retryable=True
            ) from None
        except openai.APIConnectionError:
            raise ProviderError(
                "unavailable", "connection error", retryable=True
            ) from None
        except (openai.APIError, ValueError):
            raise ProviderError(
                "unavailable", "invalid provider response", retryable=True
            ) from None
        return _parse(completion, self.model_id)


def _messages(
    system: str, transcript: Sequence[TranscriptItem]
) -> list[dict[str, Any]]:
    messages = [{"role": "system", "content": system}]
    for item in transcript:
        if isinstance(item, UserText):
            messages.append({"role": "user", "content": item.text})
        elif isinstance(item, AssistantTurn):
            response = item.response
            state = response.provider_state
            if isinstance(state, _Replay):
                messages.append(state.message)
                continue
            assistant: dict[str, Any] = {"role": "assistant", "content": response.text}
            if response.tool_calls:
                assistant["tool_calls"] = [
                    _tool_message(call.id, call.name, json.dumps(call.arguments))
                    for call in response.tool_calls
                ]
            messages.append(assistant)
        elif isinstance(item, ToolResults):
            messages.extend(
                {
                    "role": "tool",
                    "tool_call_id": result.call_id,
                    "content": result.content,
                }
                for result in item.results
            )
    return messages


@dataclass(frozen=True)
class _Replay:
    message: dict[str, Any]


def _tool_message(call_id: str, name: str, arguments: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _tokens(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def _parse(completion: ChatCompletion, configured_model: str) -> ModelResponse:
    # A compatible gateway may omit optional fields, and some return HTML on failure.
    if not isinstance(completion, ChatCompletion):
        raise ProviderError("unavailable", "invalid provider response", retryable=True)
    usage = getattr(completion, "usage", None)
    details = getattr(usage, "prompt_tokens_details", None)
    counts = Usage(
        input_tokens=_tokens(getattr(usage, "prompt_tokens", None)),
        # The completion total already includes reasoning tokens.
        output_tokens=_tokens(getattr(usage, "completion_tokens", None)),
        cache_read_input_tokens=_tokens(getattr(details, "cached_tokens", None)),
    )
    model = getattr(completion, "model", None)
    model_id = (
        model
        if isinstance(model, str) and model.isprintable() and len(model) <= 120
        else configured_model
    )
    choices = getattr(completion, "choices", None)
    choice = choices[0] if isinstance(choices, list) and choices else None
    message = getattr(choice, "message", None)
    finish = getattr(choice, "finish_reason", None)
    stop = (
        _FINISH_REASONS.get(finish, "unexpected")
        if isinstance(finish, str)
        else "unexpected"
    )
    raw_stop = finish if stop != "unexpected" else "unrecognized_finish"
    refusal = bool(getattr(message, "refusal", None)) or stop == "refusal"
    if refusal:
        return ModelResponse(
            "refusal",
            usage=counts,
            model_id=model_id,
            raw_stop_reason="refusal",
            refusal_category="content_filter"
            if finish == "content_filter"
            else "refusal",
        )
    content = getattr(message, "content", None)
    text = content if isinstance(content, str) else ""
    wire_calls = getattr(message, "tool_calls", None)
    if stop == "end_turn" and wire_calls:
        stop = "tool_use"
    if message is None or (
        stop == "end_turn" and getattr(message, "function_call", None)
    ):
        stop = "unexpected"
    calls: list[ToolCall] = []
    replay: dict[str, Any] = {"role": "assistant", "content": text}
    if stop == "tool_use":
        calls, serialized = _function_calls(wire_calls)
        if not calls:
            stop = "unexpected"
        else:
            replay["content"] = text or None
            replay["tool_calls"] = serialized
            # Some compatible reasoning models require their private reasoning on tool continuations.
            reasoning = getattr(message, "reasoning_content", None)
            if isinstance(reasoning, str) and reasoning:
                replay["reasoning_content"] = reasoning
    return ModelResponse(
        stop,
        text=text,
        tool_calls=tuple(calls),
        usage=counts,
        model_id=model_id,
        raw_stop_reason=raw_stop,
        provider_state=_Replay(replay),
    )


def _function_calls(wire_calls: Any) -> tuple[list[ToolCall], list[dict[str, Any]]]:
    calls, serialized, seen = [], [], set()
    if not isinstance(wire_calls, list):
        return calls, serialized
    for item in wire_calls:
        call_id = getattr(item, "id", None)
        fn = getattr(item, "function", None)
        name, arguments = getattr(fn, "name", None), getattr(fn, "arguments", None)
        if (
            getattr(item, "type", None) != "function"
            or not isinstance(call_id, str)
            or not call_id
            or call_id in seen
            or not isinstance(name, str)
            or not name
            or not isinstance(arguments, str)
        ):
            return [], []
        seen.add(call_id)
        try:
            parsed = json.loads(arguments)
        except (ValueError, RecursionError):
            # The existing tool registry returns a recoverable invalid-arguments result.
            parsed = None
        calls.append(ToolCall(call_id, name, parsed))
        serialized.append(_tool_message(call_id, name, arguments))
    return calls, serialized


def _status_error(error: openai.APIStatusError) -> ProviderError:
    status = error.status_code
    detail = f"HTTP {status}"
    if status == 429:
        return ProviderError("rate_limited", detail, retryable=True)
    if status in (401, 403):
        return ProviderError("auth", detail)
    if status in (408, 504):
        return ProviderError("timeout", detail, retryable=True)
    if status >= 500 or status == 409:
        return ProviderError("unavailable", detail, retryable=True)
    if status in (400, 413, 422) and error.code in (
        "context_length_exceeded",
        "context_window_exceeded",
    ):
        return ProviderError("context_exceeded", detail)
    return ProviderError("bad_request", detail)
