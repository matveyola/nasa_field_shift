"""Provider-neutral LLM interface.

The agent loop only sees these types. Gemini and Anthropic message structures stay inside their
adapter modules.
"""

from collections.abc import Sequence
from dataclasses import astuple, dataclass, field
from typing import Any, Literal, Protocol

# Normalised stop reasons. Anything the loop does not explicitly handle maps to "unexpected".
StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "context_exceeded", "unexpected"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str           # provider's id, echoed back in the matching ToolResult
    name: str
    arguments: Any    # parsed JSON as the provider returned it; validated by the tool registry


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0                  # every input token, served from the cache or not
    output_tokens: int = 0
    cache_read_input_tokens: int = 0       # the part of input_tokens served from the provider's cache
    cache_creation_input_tokens: int = 0   # the part of input_tokens written to the cache (Anthropic)
    thinking_tokens: int = 0   # reported separately by Gemini (billed as output); Anthropic counts them in output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(*(a + b for a, b in zip(astuple(self), astuple(other))))

    def cost_usd(self, input_per_mtok: float, output_per_mtok: float, cached_input_per_mtok: float | None = None) -> float:
        """Estimate from published per-million-token prices. Thinking is billed as output; input read from the cache
        at the cached price when one is given (Gemini 3.8 Flash: a tenth of the input price), else at the input price.
        Anthropic's cache-write premium is not counted. An estimate, not a bill."""
        cached = self.cache_read_input_tokens if cached_input_per_mtok is not None else 0
        return ((self.input_tokens - cached) * input_per_mtok + cached * (cached_input_per_mtok or 0)
                + (self.output_tokens + self.thinking_tokens) * output_per_mtok) / 1e6


@dataclass(frozen=True)
class ModelResponse:
    stop_reason: StopReason
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = field(default_factory=Usage)
    model_id: str = ""
    raw_stop_reason: str | None = None      # provider's own value, for logs
    refusal_category: str | None = None
    served_by_fallback: bool = False
    provider_state: Any = None              # opaque content blocks the provider replays unchanged


@dataclass(frozen=True)
class UserText:
    text: str


@dataclass(frozen=True)
class AssistantTurn:
    response: ModelResponse


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str        # JSON text
    is_error: bool = False


@dataclass(frozen=True)
class ToolResults:
    results: tuple[ToolResult, ...]


TranscriptItem = UserText | AssistantTurn | ToolResults


@dataclass(frozen=True)
class Capabilities:
    parallel_tool_calls: bool
    strict_tool_schemas: bool
    forced_tool_choice: bool  # False for Claude Opus 5.5: the loop only ever uses automatic tool choice


ProviderErrorKind = Literal["rate_limited", "unavailable", "timeout", "bad_request", "auth", "context_exceeded"]


class ProviderError(Exception):
    """A provider call failed after the provider's own retries. `detail` is safe to log."""

    def __init__(self, kind: ProviderErrorKind, detail: str = "", *, retryable: bool = False):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind, self.detail, self.retryable = kind, detail, retryable


class LLMProvider(Protocol):
    name: str
    model_id: str
    capabilities: Capabilities

    async def complete(self, *, system: str, transcript: Sequence[TranscriptItem],
                       tools: Sequence[ToolSpec], max_output_tokens: int) -> ModelResponse:
        """One model call. Raises ProviderError; cancellation must abort the request."""
        ...

    async def aclose(self) -> None: ...
