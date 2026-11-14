"""Google Gemini adapter (official `google-genai` SDK, generateContent, manual function calling).

Why generateContent: it is stateless — every call carries the whole turn's contents — so the agent loop keeps
owning the conversation, tool execution, limits and grounding, exactly as with the other providers. (The
Interactions API keeps state on Google's side; nothing here needs that.)

The SDK never runs a tool: tools are plain function declarations (no Python callables) and automatic function
calling is disabled anyway. Every call the model returns goes back to LLMAgent, which validates and runs it.

Thought signatures: Gemini attaches them to response parts (function calls, final text). The model's Content
is kept unchanged as provider_state and replayed as is in the next call of the same turn, so signatures, part
order and function-call ids survive. Function responses echo the call's id and name.
"""

import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx2
from google import genai
from google.genai import errors, types

from .provider import (
    AssistantTurn, Capabilities, ModelResponse, ProviderError, StopReason, ToolCall, ToolResults, ToolSpec,
    TranscriptItem, Usage, UserText,
)

log = logging.getLogger("agent_service.llm.gemini")

THINKING_LEVELS = ("low", "medium", "high")  # gemini-3.8-flash; "minimal" is not supported by that model
_REFUSAL = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT"}
_RETRY_CODES = [408, 429, 500, 502, 503, 504]


class GeminiUnavailable(RuntimeError):
    """Gemini cannot serve requests (no credentials). Reported by /health/ready; never replaced by another model."""

    reason = "llm_credentials_missing"


def credentials_present() -> bool:
    # The SDK reads GOOGLE_API_KEY, else GEMINI_API_KEY. Only presence is checked; the value is never read here.
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


@dataclass(frozen=True)
class _State:
    content: types.Content            # the model's turn, replayed unchanged (thought signatures included)
    call_ids: dict[str, str | None]   # our ToolCall.id -> Gemini's FunctionCall.id (None when it sent none)


class GeminiProvider:
    name = "gemini"
    # Parallel calls are possible; argument schemas are not enforced strictly by the API (the server validates).
    capabilities = Capabilities(parallel_tool_calls=True, strict_tool_schemas=False, forced_tool_choice=False)

    def __init__(self, *, model_id: str, thinking_level: str, request_timeout_s: float, max_retries: int,
                 client: genai.Client | None = None):
        if thinking_level not in THINKING_LEVELS:
            raise ValueError(f"thinking level must be one of {THINKING_LEVELS}")
        if client is None:
            if not credentials_present():
                raise GeminiUnavailable("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set: Gemini is unavailable")
            client = genai.Client(http_options=http_options(request_timeout_s, max_retries))
        self.client = client
        self.model_id = model_id
        self.thinking_level = thinking_level
        self._seq = 0

    async def aclose(self) -> None:
        await self.client.aio.aclose()

    async def complete(self, *, system: str, transcript: Sequence[TranscriptItem],
                       tools: Sequence[ToolSpec], max_output_tokens: int) -> ModelResponse:
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=[declaration(t) for t in tools])] if tools else None,
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="AUTO")) if tools else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(thinking_level=self.thinking_level.upper()),
            max_output_tokens=max_output_tokens,  # includes thinking tokens on Gemini
            # No temperature/top_p/top_k/candidate_count (deprecated or unsupported for Gemini 3.x) and no
            # Google Search tool: FAO-56 retrieval is the source workflow, and search grounding is billed.
        )
        try:
            response = await self.client.aio.models.generate_content(
                model=self.model_id, contents=contents(transcript), config=config)
        except errors.APIError as e:
            raise _error(e) from None
        except httpx2.TimeoutException:
            raise ProviderError("timeout", "request timed out", retryable=True) from None
        except httpx2.TransportError:
            raise ProviderError("unavailable", "connection error", retryable=True) from None
        return self._parse(response)

    def _parse(self, response: types.GenerateContentResponse) -> ModelResponse:
        usage = _usage(response.usage_metadata)
        model_id = response.model_version or self.model_id
        candidate = response.candidates[0] if response.candidates else None
        if candidate is None:
            blocked = response.prompt_feedback.block_reason if response.prompt_feedback else None
            reason = getattr(blocked, "value", None) or "NO_CANDIDATE"
            return ModelResponse(stop_reason="refusal" if blocked else "unexpected", usage=usage, model_id=model_id,
                                 raw_stop_reason=reason, refusal_category=reason if blocked else None)
        finish = getattr(candidate.finish_reason, "value", None) or str(candidate.finish_reason or "")
        content = candidate.content or types.Content(role="model", parts=[])
        parts = content.parts or []
        text = "".join(p.text for p in parts if p.text and not p.thought)
        calls, ids = [], {}
        for p in parts:
            if p.function_call is None:
                continue
            self._seq += 1
            our_id = p.function_call.id or f"gemini_call_{self._seq}"
            ids[our_id] = p.function_call.id
            calls.append(ToolCall(our_id, p.function_call.name or "", dict(p.function_call.args or {})))
        stop: StopReason
        if finish == "STOP":
            stop = "tool_use" if calls else "end_turn"
        elif finish == "MAX_TOKENS":
            stop = "max_tokens"
        elif finish in _REFUSAL:
            stop = "refusal"
        else:  # MALFORMED_FUNCTION_CALL, UNEXPECTED_TOOL_CALL, TOO_MANY_TOOL_CALLS, OTHER, ...: never guessed at
            stop = "unexpected"
        return ModelResponse(
            stop_reason=stop, text=text, tool_calls=tuple(calls), usage=usage, model_id=model_id,
            raw_stop_reason=finish, refusal_category=finish if stop == "refusal" else None,
            provider_state=_State(content, ids),
        )


def http_options(request_timeout_s: float, max_retries: int) -> types.HttpOptions:
    return types.HttpOptions(
        timeout=int(request_timeout_s * 1000),  # milliseconds, per attempt
        retry_options=types.HttpRetryOptions(attempts=max_retries + 1, http_status_codes=_RETRY_CODES),
    )


def declaration(spec: ToolSpec) -> types.FunctionDeclaration:
    return types.FunctionDeclaration(name=spec.name, description=spec.description,
                                     parameters_json_schema=_schema(spec.input_schema))


def _schema(node: Any) -> Any:
    """The tool's JSON Schema with `const` written as a one-value `enum` (same meaning, wider Gemini support)."""
    if isinstance(node, list):
        return [_schema(v) for v in node]
    if not isinstance(node, dict):
        return node
    out = {k: (v if k in ("enum", "required") else _schema(v)) for k, v in node.items() if k != "const"}
    if "const" in node:
        out["enum"] = [node["const"]]
    return out


def contents(transcript: Sequence[TranscriptItem]) -> list[types.Content]:
    out: list[types.Content] = []
    names: dict[str, tuple[str, str | None]] = {}  # our call id -> (tool name, Gemini call id)
    for item in transcript:
        if isinstance(item, UserText):
            out.append(types.Content(role="user", parts=[types.Part(text=item.text)]))
        elif isinstance(item, AssistantTurn):
            r, state = item.response, item.response.provider_state
            for c in r.tool_calls:
                names[c.id] = (c.name, state.call_ids.get(c.id) if isinstance(state, _State) else c.id)
            if isinstance(state, _State):
                out.append(state.content)  # unchanged: signatures, order and ids exactly as received
            else:  # a turn this provider did not produce (tests): a minimal equivalent without signatures
                parts = [types.Part(text=r.text)] if r.text else []
                parts += [types.Part(function_call=types.FunctionCall(id=c.id, name=c.name, args=c.arguments))
                          for c in r.tool_calls]
                out.append(types.Content(role="model", parts=parts))
        elif isinstance(item, ToolResults):
            # All results of one model turn go back together, each linked to its call by id and name.
            # Role "user": the live API (2026-11-10) rejects "tool" with 400 INVALID_ARGUMENT.
            out.append(types.Content(role="user", parts=[
                types.Part(function_response=types.FunctionResponse(
                    id=names.get(r.call_id, ("", None))[1], name=names.get(r.call_id, ("unknown", None))[0],
                    response=_response(r.content, r.is_error)))
                for r in item.results
            ]))
    return out


def _response(content: str, is_error: bool) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except ValueError:
        value = content
    if isinstance(value, dict):
        return value
    return {"error": value} if is_error else {"output": value}


def _usage(meta: types.GenerateContentResponseUsageMetadata | None) -> Usage:
    if meta is None:
        return Usage()
    return Usage(
        input_tokens=(meta.prompt_token_count or 0) + (meta.tool_use_prompt_token_count or 0),
        output_tokens=meta.candidates_token_count or 0,
        thinking_tokens=meta.thoughts_token_count or 0,
        cache_read_input_tokens=meta.cached_content_token_count or 0,  # a part of input_tokens on Gemini
    )


def _error(e: errors.APIError) -> ProviderError:
    # Status code and status name only: error bodies are never logged.
    code, status = e.code or 0, e.status or ""
    detail = f"HTTP {code} {status}".strip()
    message = (e.message or "").lower()
    if code == 429:
        return ProviderError("rate_limited", detail, retryable=True)
    if code in (401, 403):
        return ProviderError("auth", detail)
    if code in (408, 504):
        return ProviderError("timeout", detail, retryable=True)
    if code >= 500:
        return ProviderError("unavailable", detail, retryable=True)
    if code == 400 and "token" in message and any(w in message for w in ("exceed", "too long", "limit")):
        return ProviderError("context_exceeded", detail)
    return ProviderError("bad_request", detail)
