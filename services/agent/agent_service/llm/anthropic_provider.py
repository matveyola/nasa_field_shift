"""Anthropic Claude adapter (official `anthropic` SDK, Messages API, manual tool loop).

A manual loop (not the SDK tool runner) because the agent owns tool execution, per-call
tracing, limits, and grounding, and because the loop must stay provider-neutral.
"""

import logging
from collections.abc import Sequence
from typing import Any

import anthropic

from .provider import (
    AssistantTurn, Capabilities, ModelResponse, ProviderError, StopReason, ToolCall, ToolResults, ToolSpec,
    TranscriptItem, Usage, UserText,
)

log = logging.getLogger("agent_service.llm.anthropic")

FALLBACK_BETA = "server-side-fallback-2026-07-01"   # pairs with fallbacks="default"
_STOP: dict[str | None, StopReason] = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
    "model_context_window_exceeded": "context_exceeded",
}
# Blocks that must not be echoed when they precede a fallback switch point.
_PRE_FALLBACK_DROP = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}


class AnthropicProvider:
    name = "anthropic"
    # Claude Opus 5.5: forced tool_choice ("any"/"tool") returns a 400, so the loop never sends it.
    capabilities = Capabilities(parallel_tool_calls=True, strict_tool_schemas=True, forced_tool_choice=False)

    def __init__(self, *, model_id: str, effort: str, request_timeout_s: float, max_retries: int,
                 refusal_fallback: bool, strict_tools: bool, client: anthropic.AsyncAnthropic | None = None):
        # Credentials come from the SDK's own resolution (ANTHROPIC_API_KEY, auth token, profile);
        # this service never reads, stores or logs them.
        self.client = client or anthropic.AsyncAnthropic(timeout=request_timeout_s, max_retries=max_retries)
        self.model_id = model_id
        self.effort = effort
        self.refusal_fallback = refusal_fallback
        self.strict_tools = strict_tools

    async def aclose(self) -> None:
        await self.client.close()

    async def complete(self, *, system: str, transcript: Sequence[TranscriptItem],
                       tools: Sequence[ToolSpec], max_output_tokens: int) -> ModelResponse:
        params: dict[str, Any] = dict(
            model=self.model_id,
            max_tokens=max_output_tokens,
            # Stable system prompt (and the tool list rendered before it) is the cacheable prefix.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=self._messages(transcript),
            tools=[self._tool(t) for t in tools],
            # Thinking is always on for Opus 5.5 (adaptive); effort is the only depth control
            # and defaults to medium there, so it is set explicitly. tool_choice stays "auto".
            output_config={"effort": self.effort},
        )
        if self.refusal_fallback:
            params.update(betas=[FALLBACK_BETA], fallbacks="default")
        try:
            message = await self.client.beta.messages.create(**params)
        except anthropic.RateLimitError as e:
            raise ProviderError("rate_limited", _status(e), retryable=True) from None
        except anthropic.APITimeoutError:
            raise ProviderError("timeout", "request timed out", retryable=True) from None
        except anthropic.APIConnectionError:
            raise ProviderError("unavailable", "connection error", retryable=True) from None
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise ProviderError("auth", _status(e)) from None
        except (anthropic.InternalServerError, anthropic.OverloadedError) as e:
            raise ProviderError("unavailable", _status(e), retryable=True) from None
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                raise ProviderError("unavailable", _status(e), retryable=True) from None
            raise ProviderError("bad_request", _status(e)) from None
        return self._parse(message)

    # ------------------------------------------------------------------ conversion

    def _tool(self, spec: ToolSpec) -> dict[str, Any]:
        tool = {"name": spec.name, "description": spec.description, "input_schema": spec.input_schema}
        if self.strict_tools:
            tool["strict"] = True  # schema-valid arguments; the server still re-validates everything
        return tool

    @staticmethod
    def _messages(transcript: Sequence[TranscriptItem]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for item in transcript:
            if isinstance(item, UserText):
                messages.append({"role": "user", "content": [{"type": "text", "text": item.text}]})
            elif isinstance(item, AssistantTurn):
                messages.append({"role": "assistant", "content": _replay_content(item.response)})
            elif isinstance(item, ToolResults):
                # All results of one assistant turn go back in a single user message.
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content, "is_error": r.is_error}
                    for r in item.results
                ]})
        return messages

    def _parse(self, message: Any) -> ModelResponse:
        blocks = list(message.content)
        boundary = max((i for i, b in enumerate(blocks) if b.type == "fallback"), default=-1)
        effective = blocks[boundary + 1:]  # only what the serving model produced after the last switch
        usage = message.usage
        details = getattr(message, "stop_details", None)
        return ModelResponse(
            stop_reason=_STOP.get(message.stop_reason, "unexpected"),
            text="".join(b.text for b in effective if b.type == "text"),
            tool_calls=tuple(ToolCall(b.id, b.name, b.input) for b in effective if b.type == "tool_use"),
            usage=Usage(
                # Anthropic reports cache reads and writes apart from input_tokens; Usage counts every input token.
                input_tokens=(usage.input_tokens or 0) + (usage.cache_read_input_tokens or 0)
                + (usage.cache_creation_input_tokens or 0),
                output_tokens=usage.output_tokens or 0,
                cache_read_input_tokens=usage.cache_read_input_tokens or 0,
                cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
            ),
            model_id=message.model,
            raw_stop_reason=message.stop_reason,
            refusal_category=getattr(details, "category", None) if message.stop_reason == "refusal" else None,
            served_by_fallback=any(getattr(i, "type", None) == "fallback_message" for i in (usage.iterations or [])),
            provider_state=blocks,
        )


def _replay_content(response: ModelResponse) -> list[Any]:
    """Assistant content to send back: the original blocks unchanged (thinking blocks included),
    minus model-internal blocks that precede a fallback switch point."""
    blocks = response.provider_state
    if blocks is None:  # a turn this provider did not produce (tests): rebuild a minimal equivalent
        content: list[Any] = [{"type": "text", "text": response.text}] if response.text else []
        content += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in response.tool_calls]
        return content
    boundary = max((i for i, b in enumerate(blocks) if b.type == "fallback"), default=-1)
    return [b for i, b in enumerate(blocks) if i > boundary or b.type not in _PRE_FALLBACK_DROP]


def _status(error: anthropic.APIStatusError) -> str:
    # Status code and error type only: response bodies are never logged.
    return f"HTTP {error.status_code} {getattr(error, 'type', None) or ''}".strip()
