"""The agent abstraction: what the application service needs from any agent implementation.

M1 ships one implementation (`DeterministicDevAgent`). M2 adds an LLM-backed agent that
implements the same `respond` method.
"""

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from contracts.models import (
    AgentMessage, AgentRequest, ErrorCode, Fact, TextDeltaEvent, ToolFinishedEvent, ToolStartedEvent,
)

from .config import Settings
from .integrations.normalize import ComparisonRecord


@dataclass(frozen=True)
class TurnEvidence:
    """Verified outputs of this turn's external tools (geo profile facts, simulation results).

    Internal: the service uses it to accept facts and result references that are not in the
    request's FieldContext. It is never streamed to the client.
    """

    facts: tuple[Fact, ...] = ()
    comparisons: tuple[ComparisonRecord, ...] = ()


# Progress events the agent may stream, optionally one TurnEvidence, then exactly one AgentMessage.
# `turn.started`, `turn.completed` and `error` are owned by the service, never the agent.
AgentOutput = TextDeltaEvent | ToolStartedEvent | ToolFinishedEvent | TurnEvidence | AgentMessage


class AgentFailure(Exception):
    """A controlled, public-safe failure an agent reports instead of a message.

    The service turns it into exactly one `error` event with these fields.
    """

    def __init__(self, code: ErrorCode, message_key: str, *, retryable: bool):
        super().__init__(f"{code}: {message_key}")
        self.code, self.message_key, self.retryable = code, message_key, retryable


class OpenAIUnavailable(RuntimeError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class Agent(Protocol):
    name: str  # reported by /health/ready and in logs

    def respond(self, request: AgentRequest, turn_id: str) -> AsyncIterator[AgentOutput]:
        """Run one turn.

        Contract for implementations:
        - yield progress events, optionally one `TurnEvidence`, then one `AgentMessage` with `turn_id`, then stop;
        - never mutate `request` and keep no per-request state on `self`
          (one instance serves concurrent requests);
        - stop promptly when the iterator is closed or cancelled.
        """
        ...


def build_agent(settings: Settings) -> Agent:
    """Build the configured agent once, at application startup (clients are reused across requests)."""
    if settings.agent in ("deterministic", "deterministic-dev"):
        from .dev_agent import DeterministicDevAgent

        return DeterministicDevAgent()

    if settings.agent == "gemini":
        from .llm.gemini_provider import GeminiUnavailable, credentials_present

        if not credentials_present():  # checked before anything else is opened
            raise GeminiUnavailable("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set: Gemini is unavailable")
    openai_key = ""
    if settings.agent == "openai":
        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not openai_key:
            raise OpenAIUnavailable("llm_credentials_missing", "OPENAI_API_KEY is not set")
        if settings.openai_base_url is None or settings.openai_model is None:
            raise OpenAIUnavailable(
                "llm_configuration_missing", "AGENT_OPENAI_BASE_URL and AGENT_OPENAI_MODEL are required"
            )

    from .llm_agent import LLMAgent, LoopLimits

    limits = LoopLimits(
        max_rounds=settings.llm_max_rounds, max_tool_calls=settings.llm_max_tool_calls,
        max_tool_calls_per_round=settings.llm_max_tool_calls_per_round,
        max_output_tokens=settings.llm_max_output_tokens, max_context_chars=settings.llm_max_context_chars,
        max_repairs=settings.llm_max_repairs, tool_timeout_s=settings.llm_tool_timeout_s,
    )
    knowledge = load_knowledge(settings)
    from .integrations.registry import IntegrationRegistry

    integrations = IntegrationRegistry.from_settings(settings)
    weather = None
    if settings.weather_provider == "open-meteo":
        from .weather import OpenMeteoClient

        weather = OpenMeteoClient(settings.weather_url, settings.weather_timeout_s)
    if settings.agent == "scripted-llm":
        from .llm.scripted import ScriptedProvider, demo_script

        return LLMAgent(ScriptedProvider(demo_script, record_requests=False), limits, knowledge=knowledge,
                        knowledge_timeout_s=settings.rag_timeout_s, integrations=integrations, weather=weather)
    prices = (settings.llm_price_input_usd_per_mtok, settings.llm_price_output_usd_per_mtok)
    prices = (*prices, settings.llm_price_cached_input_usd_per_mtok) if None not in prices else None
    if settings.agent == "openai":
        from .llm.openai_provider import OpenAIProvider

        provider = OpenAIProvider(
            api_key=openai_key,
            base_url=settings.openai_base_url,
            model_id=settings.openai_model,
            request_timeout_s=settings.llm_request_timeout_s,
            max_retries=settings.llm_max_retries,
            token_limit_field=settings.openai_token_limit_field,
            reasoning_effort=settings.openai_reasoning_effort,
        )
        return LLMAgent(provider, limits, knowledge=knowledge, knowledge_timeout_s=settings.rag_timeout_s,
                        integrations=integrations, prices=prices, weather=weather)
    if settings.agent == "gemini":
        from .llm.gemini_provider import GeminiProvider

        # No key -> GeminiUnavailable: the service reports not_ready (llm_credentials_missing). Never another model.
        provider = GeminiProvider(model_id=settings.gemini_model, thinking_level=settings.gemini_thinking_level,
                                  request_timeout_s=settings.llm_request_timeout_s, max_retries=settings.llm_max_retries)
        return LLMAgent(provider, limits, knowledge=knowledge, knowledge_timeout_s=settings.rag_timeout_s,
                        integrations=integrations, prices=prices, weather=weather)
    if settings.agent == "anthropic":
        import logging

        from .llm.anthropic_provider import AnthropicProvider

        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            # The SDK may still find a profile or workload identity; if not, every turn fails with error.llm_auth.
            logging.getLogger("agent_service").warning(
                "anthropic agent: ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN not set; relying on other SDK credentials")

        provider = AnthropicProvider(
            model_id=settings.llm_model, effort=settings.llm_effort,
            request_timeout_s=settings.llm_request_timeout_s, max_retries=settings.llm_max_retries,
            refusal_fallback=settings.llm_refusal_fallback, strict_tools=settings.llm_strict_tools,
        )
        return LLMAgent(provider, limits, knowledge=knowledge, knowledge_timeout_s=settings.rag_timeout_s,
                        integrations=integrations, prices=prices, weather=weather)
    raise ValueError(f"unknown agent implementation: {settings.agent!r}")


def load_knowledge(settings: Settings):
    """Load the prebuilt index (and models for the configured mode) once. None when off or unavailable."""
    import logging
    from pathlib import Path

    log = logging.getLogger("agent_service.knowledge")
    if settings.rag_mode == "off":
        return None
    from .knowledge.retrieve import KnowledgeBase, KnowledgeUnavailable

    try:
        kb = KnowledgeBase(Path(settings.rag_index_path), mode=settings.rag_mode, model_cache=settings.rag_model_cache,
                           reranker_dtype=settings.rag_reranker_dtype, rerank_top=settings.rag_rerank_top)
    except KnowledgeUnavailable as e:
        if settings.rag_required:
            raise
        log.warning("knowledge base unavailable: %s (search_knowledge disabled)", e)
        return None
    status = kb.status()
    if status["mode"] != settings.rag_mode:
        log.warning("knowledge base degraded: requested %s, running %s: %s", settings.rag_mode, status["mode"],
                    "; ".join(status["problems"]))
    else:
        log.info("knowledge base ready mode=%s chunks=%d", status["mode"], status["chunks"])
    return kb
