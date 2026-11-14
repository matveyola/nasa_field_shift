"""Application service: runs one agent turn and turns it into contract events.

No HTTP here. The API layer calls `stream()` (SSE) or `respond()` (single JSON message).
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime

from pydantic import ValidationError

from contracts.models import (
    AgentEvent,
    AgentMessage,
    AgentRequest,
    ErrorCode,
    ErrorEvent,
    FieldContext,
    FocusChartAction,
    OpenPanelAction,
    SelectRotationsAction,
    SetLayersAction,
    TextDeltaEvent,
    ToolFinishedEvent,
    ToolStartedEvent,
    TurnCompletedEvent,
    TurnStartedEvent,
    UIActionEnvelope,
)

from .agent import Agent, AgentFailure, TurnEvidence
from .attestation import UNTRUSTED, RequestTrust
from .integrations.normalize import ComparisonRecord

log = logging.getLogger("agent_service.turn")

_PROGRESS = (TextDeltaEvent, ToolStartedEvent, ToolFinishedEvent)


class AgentContractError(Exception):
    """The agent produced output that breaks the contract or the grounding rules."""


class ServiceError(Exception):
    """A turn ended in a public, safe-to-show error."""

    def __init__(self, error: ErrorEvent):
        super().__init__(error.code)
        self.error = error


def ui_action_problem(envelope: UIActionEnvelope, ctx: FieldContext,
                      results: Mapping[str, ComparisonRecord] | None = None) -> str | None:
    """Why this action must not reach the host, or None if it references only what exists.

    `results` are the simulation results verified in this turn; result references (comparison
    panel ref_id, rotation ids) must point at one of them, for this field and revision, unexpired.
    """
    now = datetime.now(UTC)
    live = {rid: r for rid, r in (results or {}).items()
            if r.field_id == ctx.field_id and r.field_context_revision == ctx.revision and r.expires_at > now}
    if envelope.field_context_revision != ctx.revision:
        return f"stale revision {envelope.field_context_revision} (context is {ctx.revision})"
    action = envelope.action
    if isinstance(action, SetLayersAction):
        if missing := set(action.layer_ids) - {layer.layer_id for layer in ctx.available_layers}:
            return f"unknown layer ids {sorted(missing)}"
    elif isinstance(action, FocusChartAction):
        charts = {c.chart_id: set(c.metric_ids) for c in ctx.available_charts}
        if action.chart_id not in charts:
            return f"unknown chart id {action.chart_id!r}"
        if missing := set(action.metric_ids) - charts[action.chart_id]:
            return f"unknown metric ids {sorted(missing)}"
    elif isinstance(action, SelectRotationsAction):
        if not live:
            return "rotation ids can only come from a completed simulation result of this turn"
        known = {rid for r in live.values() for rid in r.rotation_ids}
        if missing := set(action.rotation_ids) - known:
            return f"unknown rotation ids {sorted(missing)}"
    elif isinstance(action, OpenPanelAction) and action.panel == "comparison" and action.ref_id is not None:
        if action.ref_id not in live:
            return "unknown, expired or foreign comparison result id"
        if action.scenario is not None and live[action.ref_id].scenario != action.scenario:
            return "comparison result is for another scenario"
    return None


class AgentService:
    def __init__(self, agent: Agent, turn_timeout_s: float, log_message_text: bool = False):
        self.agent = agent
        self.turn_timeout_s = turn_timeout_s
        self.log_message_text = log_message_text

    async def stream(self, request: AgentRequest, trust: RequestTrust = UNTRUSTED) -> AsyncIterator[AgentEvent]:
        """Yield `turn.started`, progress events, then exactly one `turn.completed` or `error`.

        Never raises for agent failures; cancellation (client disconnect) propagates and
        closes the agent iterator.
        """
        turn_id = f"turn_{uuid.uuid4().hex}"
        started = time.monotonic()
        yield TurnStartedEvent(event="turn.started", turn_id=turn_id)

        # Trust is passed only when the host signed this request; an agent that cannot take it fails closed.
        agen = self.agent.respond(request, turn_id, trust=trust) if trust.attested else self.agent.respond(request, turn_id)
        evidence = TurnEvidence()
        outcome = "completed"
        try:
            while True:
                remaining = started + self.turn_timeout_s - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                try:
                    item = await asyncio.wait_for(anext(agen), remaining)
                except StopAsyncIteration:
                    raise AgentContractError("agent stopped without a final message") from None
                if isinstance(item, TurnEvidence):
                    evidence = item  # internal: widens what the final checks accept; never streamed
                    continue
                if isinstance(item, AgentMessage):
                    terminal: AgentEvent = TurnCompletedEvent(
                        event="turn.completed", message=self._finalize(request, turn_id, item, started, evidence)
                    )
                    break
                if not isinstance(item, _PROGRESS):
                    raise AgentContractError(f"agent yielded {type(item).__name__}")
                yield type(item).model_validate_json(item.model_dump_json())
        except TimeoutError:
            outcome = "timeout"
            terminal = self._error(request, "upstream_timeout", "error.agent_timeout", retryable=True)
        except (asyncio.CancelledError, GeneratorExit):
            outcome = "cancelled"  # client disconnected or stopped reading
            raise
        except AgentFailure as failure:  # controlled: provider errors, loop limits, incomplete output
            outcome = f"failed:{failure.message_key}"
            terminal = self._error(request, failure.code, failure.message_key, failure.retryable)
        except (AgentContractError, ValidationError) as exc:
            outcome = "contract_violation"
            log.error("agent output rejected turn_id=%s request_id=%s reason=%s", turn_id, request.request_id, exc)
            terminal = self._error(request, "internal", "error.internal", retryable=False)
        except Exception:
            outcome = "agent_error"
            log.exception("agent failed turn_id=%s request_id=%s", turn_id, request.request_id)
            terminal = self._error(request, "internal", "error.internal", retryable=False)
        finally:
            await agen.aclose()
            self._log_turn(request, turn_id, outcome, started)
        yield terminal

    async def respond(self, request: AgentRequest, trust: RequestTrust = UNTRUSTED) -> AgentMessage:
        """Run a turn to completion and return the final message, or raise ServiceError."""
        async with contextlib.aclosing(self.stream(request, trust)) as events:
            async for event in events:
                if isinstance(event, TurnCompletedEvent):
                    return event.message
                if isinstance(event, ErrorEvent):
                    raise ServiceError(event)
        raise AssertionError("stream() ended without a terminal event")

    def _finalize(self, request: AgentRequest, turn_id: str, message: AgentMessage, started: float,
                  evidence: TurnEvidence = TurnEvidence()) -> AgentMessage:
        ctx = request.field_context
        if message.turn_id != turn_id:
            raise AgentContractError("message turn_id does not match the turn")

        # Facts must be relayed verbatim from the request context or from this turn's verified
        # external results: no new numbers, no upgraded data_status, no other field's data.
        known = {f.id: f for f in ctx.facts} | {f.id: f for f in evidence.facts}
        for fact in message.facts:
            if known.get(fact.id) != fact:
                raise AgentContractError(f"fact {fact.id!r} is not from the field context or a verified tool result")
        results = {r.result_id: r for r in evidence.comparisons}

        # Farmer-sourced constraints must quote text the farmer actually wrote.
        farmer_text = [request.message.text or ""] + [h.text for h in request.history if h.role == "user"]
        proposed = list(message.constraints_proposed)
        for envelope in message.ui_actions:
            proposed += getattr(envelope.action, "constraints", [])
        for c in proposed:
            if c.source == "farmer_text" and not any(c.evidence and c.evidence in t for t in farmer_text):
                raise AgentContractError(f"constraint {c.id!r} evidence is not in the farmer's text")

        actions = []
        for envelope in message.ui_actions:
            if problem := ui_action_problem(envelope, ctx, results):
                log.warning("dropped ui action turn_id=%s action=%s reason=%s", turn_id, envelope.action.type, problem)
                continue
            actions.append(envelope)

        latency_ms = int((time.monotonic() - started) * 1000)
        final = message.model_copy(update={
            "ui_actions": actions,
            "meta": message.meta.model_copy(update={"latency_ms": latency_ms}),
        })
        # Re-check the whole message in wire form: catches anything built with model_construct
        # or mutated after validation.
        return AgentMessage.model_validate_json(final.model_dump_json())

    @staticmethod
    def _error(request: AgentRequest, code: ErrorCode, message_key: str, retryable: bool) -> ErrorEvent:
        return ErrorEvent(event="error", code=code, message_key=message_key, retryable=retryable,
                          request_id=request.request_id)

    def _log_turn(self, request: AgentRequest, turn_id: str, outcome: str, started: float) -> None:
        text = request.message.text or ""
        extra = f" text={text!r}" if self.log_message_text else ""
        log.info(
            "turn turn_id=%s request_id=%s session_id=%s field_id=%s outcome=%s duration_ms=%d text_chars=%d%s",
            turn_id, request.request_id, request.session_id, request.field_context.field_id,
            outcome, (time.monotonic() - started) * 1000, len(text), extra,
        )
