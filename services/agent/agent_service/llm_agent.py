"""LLM-backed agent: a bounded tool loop around a provider-neutral model.

Per turn:
  build context envelope -> model call -> (validate + run tools -> results back)* -> final text
  -> grounding check (one repair round) -> validated AgentMessage.
Text is streamed only after it passed grounding ("validated incremental delivery", not raw
token streaming). Tool events are streamed as tools run.
"""

import logging
import time
import typing
from collections.abc import AsyncIterator
from dataclasses import dataclass

from contracts.models import (
    AgentMessage, AgentRequest, AgentWarning, ClarifyingQuestionBlock, ResponseMeta, TextBlock, TextDeltaEvent,
    ToolFinishedEvent, ToolName, ToolStartedEvent, block_fact_ids,
)

from . import citations, scenarios
from .agent import AgentFailure, AgentOutput, TurnEvidence
from .attestation import UNTRUSTED, RequestTrust
from .grounding import Grounded, check, is_farmer_arithmetic, paragraph_chunks, redact, visible_text
from .llm.provider import AssistantTurn, LLMProvider, ProviderError, ToolResult, ToolResults, Usage, UserText
from .prompts import SYSTEM_PROMPT, ContextTooLarge, prompt_version, repair_message, response_language, user_message
from .tools import EXPLAINED_REFUSALS, Tool, TurnState, default_registry, execute, never_runs

log = logging.getLogger("agent_service.llm_agent")

MAX_MARKDOWN = 20_000
CANNED = {
    "refusal": {"uk": "Вибачте, з цим запитом я не можу допомогти. Спробуйте поставити питання про ваше поле інакше.",
                "en": "Sorry, I can't help with that request. Try asking about your field in a different way."},
    "empty": {"uk": "Не вдалося сформувати перевірену відповідь. Спробуйте переформулювати питання.",
              "en": "I could not produce a verified answer. Please try rephrasing the question."},
}
_FAILURES: dict[str, tuple[str, str, bool]] = {
    "rate_limited": ("rate_limited", "error.llm_rate_limited", True),
    "timeout": ("upstream_timeout", "error.llm_timeout", True),
    "unavailable": ("upstream_unavailable", "error.llm_unavailable", True),
    "auth": ("llm_error", "error.llm_auth", False),
    "bad_request": ("llm_error", "error.llm_bad_request", False),
    "context_exceeded": ("invalid_request", "error.context_too_large", False),
}


@dataclass(frozen=True)
class LoopLimits:
    max_rounds: int = 6               # model calls per turn, repairs included
    max_tool_calls: int = 12          # per turn
    max_tool_calls_per_round: int = 6
    max_output_tokens: int = 4096     # per model call
    max_context_chars: int = 60_000   # system + user message (a char budget, not tokens)
    max_repairs: int = 1              # grounding rewrites before redaction
    tool_timeout_s: float = 5.0


class LLMAgent:
    def __init__(self, provider: LLMProvider, limits: LoopLimits = LoopLimits(),
                 registry: dict[str, Tool] | None = None, knowledge=None, knowledge_timeout_s: float = 20.0,
                 integrations=None, prices: tuple[float | None, ...] | None = None, weather=None):
        self.provider = provider
        self.prices = prices  # (input, output[, cached input]) USD per million tokens, for a logged estimate only
        self.limits = limits
        self.knowledge = knowledge  # KnowledgeBase or None; shared, read-only, loaded once at startup
        self.integrations = integrations  # IntegrationRegistry or None; geo / simulation services
        self.weather = weather  # OpenMeteoClient or None (agent_service/weather.py)
        self.registry = registry or default_registry(knowledge_timeout_s, with_knowledge=knowledge is not None,
                                                     integrations=integrations, with_weather=weather is not None)
        unknown = set(self.registry) - set(typing.get_args(ToolName))
        if unknown:
            raise ValueError(f"tools not in the M0 ToolName contract: {sorted(unknown)}")
        self.specs = [t.spec for t in self.registry.values()]
        self.name = f"llm/{provider.name}"
        self.prompt_version = prompt_version(self.specs)

    async def aclose(self) -> None:
        await self.provider.aclose()
        if self.integrations is not None:
            await self.integrations.aclose()
        if self.weather is not None:
            await self.weather.aclose()

    async def _tools_for_turn(self) -> tuple[dict[str, Tool], dict]:
        """The tools offered this turn: compare_rotations only while the service reports the capability."""
        tools = dict(self.registry)
        services: dict = {"rotation_simulation": {"available": False}, "field_profile_service": {"available": False}}
        if self.integrations is not None:
            sim = await self.integrations.capabilities("simulation")
            if sim and sim.simulation and sim.simulation.compare_rotations and sim.simulation.scenarios:
                services["rotation_simulation"] = {"available": True, "service_mode": sim.service.mode,
                                                   "scenarios": list(sim.simulation.scenarios)}
            geo = await self.integrations.capabilities("geo") if self.integrations.geo_mode != "off" else None
            if geo:
                services["field_profile_service"] = {"available": True, "service_mode": geo.service.mode}
        if not services["rotation_simulation"]["available"]:
            tools.pop("compare_rotations", None)
        return tools, services

    async def respond(self, request: AgentRequest, turn_id: str, trust: RequestTrust = UNTRUSTED) -> AsyncIterator[AgentOutput]:
        tools, services = await self._tools_for_turn()
        if request.client.capabilities is None:
            # A v0.1 client (no capabilities: the Field Shift API host) shows only text and questions: no cards,
            # tables or charts, so the values go in the text, grounded as usual.
            tools.pop("present_blocks", None)
        specs = [t.spec for t in tools.values()]
        state = TurnState(request=request, turn_id=turn_id, knowledge=self.knowledge, integrations=self.integrations,
                          weather=self.weather,
                          context_attested=trust.attested, isolation_scope=trust.isolation_scope)
        lang = response_language(request)
        try:
            transcript: list = [UserText(user_message(request, lang, self.limits.max_context_chars - len(SYSTEM_PROMPT),
                                                      knowledge="search_knowledge" in tools, services=services))]
        except ContextTooLarge:
            raise AgentFailure("invalid_request", "error.context_too_large", retryable=False) from None

        usage, model_id, repairs, stalled, seq = Usage(), self.provider.model_id, 0, 0, 0
        for round_no in range(1, self.limits.max_rounds + 1):
            started = time.monotonic()
            try:
                response = await self.provider.complete(system=SYSTEM_PROMPT, transcript=transcript,
                                                        tools=specs, max_output_tokens=self.limits.max_output_tokens)
            except ProviderError as e:
                log.warning("llm call failed turn_id=%s round=%d kind=%s detail=%s", turn_id, round_no, e.kind, e.detail)
                code, key, retryable = _FAILURES[e.kind]
                raise AgentFailure(code, key, retryable=retryable) from None
            usage += response.usage
            model_id = response.model_id or model_id
            log.info("llm round turn_id=%s round=%d provider=%s model=%s stop=%s tool_calls=%d in=%d out=%d think=%d "
                     "cache_read=%d fallback=%s duration_ms=%d", turn_id, round_no, self.provider.name, model_id,
                     response.raw_stop_reason or response.stop_reason, len(response.tool_calls),
                     response.usage.input_tokens, response.usage.output_tokens, response.usage.thinking_tokens,
                     response.usage.cache_read_input_tokens, response.served_by_fallback,
                     (time.monotonic() - started) * 1000)
            transcript.append(AssistantTurn(response))

            if response.stop_reason == "refusal":
                log.info("llm refusal turn_id=%s category=%s", turn_id, response.refusal_category)
                # Nothing proposed earlier in the turn is kept: a declined turn proposes nothing.
                message = self._message(request, turn_id, lang, CANNED["refusal"][lang], TurnState(request, turn_id),
                                        model_id, [], extra=[AgentWarning(code="low_confidence",
                                                                          detail="The model declined this request.")])
                yield TextDeltaEvent(event="text.delta", text=message.blocks[0].markdown)
                yield message
                return
            if response.stop_reason == "max_tokens":   # incomplete: never treated as an answer or run as tools
                raise AgentFailure("llm_error", "error.llm_incomplete", retryable=True)
            if response.stop_reason == "context_exceeded":
                raise AgentFailure("invalid_request", "error.context_too_large", retryable=False)
            if response.stop_reason == "unexpected" or (response.stop_reason == "tool_use" and not response.tool_calls):
                raise AgentFailure("llm_error", "error.llm_unexpected_stop", retryable=False)

            if response.stop_reason == "tool_use":
                results, progress = [], False
                for i, tool_call in enumerate(response.tool_calls):
                    if i >= self.limits.max_tool_calls_per_round or state.tool_calls >= self.limits.max_tool_calls:
                        results.append(ToolResult(tool_call.id, '{"error": {"category": "tool_budget", "message": '
                                                  '"Tool call limit reached for this answer. Answer with what you have."}}',
                                                  is_error=True))
                        continue
                    state.tool_calls += 1
                    seq += 1
                    call_id = f"call_{seq}"
                    # A call that is refused before it runs (unknown name, malformed arguments, an identical repeat) is
                    # the model's own slip: no step for the farmer. An explained refusal is shown as done (see
                    # tools.EXPLAINED_REFUSALS); the trace keeps the real outcome either way.
                    known = not never_runs(tool_call, state, tools)
                    if known:
                        yield ToolStartedEvent(event="tool.started", call_id=call_id, tool=tool_call.name)
                    result, trace = await execute(tool_call, state, tools, call_id, self.limits.tool_timeout_s)
                    if known:
                        status = ("ok" if trace.status == "ok" or trace.error_category in EXPLAINED_REFUSALS
                                  else "timeout" if trace.error_category == "timeout" else "error")
                        yield ToolFinishedEvent(event="tool.finished", call_id=call_id, status=status,
                                                latency_ms=trace.duration_ms)
                    results.append(result)
                    progress |= trace.status == "ok"
                transcript.append(ToolResults(tuple(results)))
                stalled = 0 if progress else stalled + 1
                if stalled >= 2:  # repeated failing or identical calls: stop instead of looping
                    raise AgentFailure("llm_error", "error.agent_no_progress", retryable=False)
                continue

            # end_turn: ground the draft before anything reaches the farmer (without invisible characters that
            # could hide a number from the checks).
            draft = visible_text(response.text)
            grounded = self._ground(draft, state, lang)
            if (grounded.violations or not grounded.markdown.strip()) and repairs < self.limits.max_repairs \
                    and round_no < self.limits.max_rounds:
                repairs += 1
                log.info("grounding repair turn_id=%s violations=%s", turn_id,
                         sorted({v.category for v in grounded.violations}) or ["empty_answer"])
                transcript.append(UserText(repair_message(grounded.violations) if grounded.violations else
                                           "<grounding_check>\nYour answer was empty. Answer the farmer briefly.\n</grounding_check>"))
                continue
            extra = []
            if grounded.violations:
                categories = sorted({v.category for v in grounded.violations})
                log.warning("grounding redaction turn_id=%s violations=%s", turn_id, categories)
                grounded = self._ground(redact(draft, grounded.violations), state, lang)
                extra.append(AgentWarning(code="ungrounded_number",
                                          detail=f"Unsupported statements were removed ({', '.join(categories)})."[:300]))
            text = grounded.markdown.strip() if not grounded.violations else ""
            if not text:
                text, grounded.fact_ids, grounded.citations = CANNED["empty"][lang], [], []
                extra.append(AgentWarning(code="low_confidence", detail="No verified answer could be produced."))
            weak = citations.unrelated_chunks(request.message.text or "", [state.evidence[c.chunk_id] for c in grounded.citations
                                                                           if c.chunk_id in state.evidence])
            if weak:  # structurally valid is not the same as relevant: say so instead of implying sufficiency
                extra.append(AgentWarning(code="low_confidence", detail="Some cited passages share no key terms with the "
                                          "question: treat them as background, not as a direct answer."))
            # Token counts are the provider's own numbers; never the farmer's text.
            log.info("turn usage turn_id=%s provider=%s model=%s rounds=%d tool_calls=%d in=%d out=%d think=%d "
                     "cache_read=%d est_usd=%s", turn_id, self.provider.name, model_id, round_no, state.tool_calls,
                     usage.input_tokens, usage.output_tokens, usage.thinking_tokens, usage.cache_read_input_tokens,
                     f"{usage.cost_usd(*self.prices):.6f}" if self.prices else "n/a")
            message = self._message(request, turn_id, lang, text, state, model_id, grounded.fact_ids, extra=extra,
                                    citations=grounded.citations)
            if state.external_facts or state.comparisons:
                yield TurnEvidence(facts=tuple(state.external_facts.values()), comparisons=tuple(state.comparisons.values()))
            for chunk in paragraph_chunks(message.blocks[0].markdown):
                yield TextDeltaEvent(event="text.delta", text=chunk)
            yield message
            return
        raise AgentFailure("llm_error", "error.agent_round_limit", retryable=False)

    def _ground(self, text: str, state: TurnState, lang: str) -> Grounded:
        unverified = state.unverified_real_fact_ids()
        return check(
            text, retrieved=state.retrieved, known_ids=set(state.facts), farmer_text=state.farmer_message(),
            simulation_ran=bool(state.comparisons),  # only a verified compare_rotations result counts
            # Only a confirmed live value may back a "live / real-time" claim; a request saying "live" is not proof.
            # The farmer's own figures and arithmetic on them are "live" for this turn but are not live data.
            any_live_fact=any(f.provenance.data_status == "live" and fid not in unverified
                              and not is_farmer_arithmetic(f) for fid, f in state.retrieved.items()), lang=lang,
            # Every value of the field and of this turn is demo data: "actual observations" would be false.
            demo_only=bool(seen := [*state.facts.values(), *state.retrieved.values()])
            and all(f.provenance.data_status in ("fixture", "unavailable") for f in seen),
            evidence=state.evidence,
            index_has=(lambda cid: cid in self.knowledge.chunks) if self.knowledge is not None else None,
            unverified=unverified,
        )

    def _message(self, request: AgentRequest, turn_id: str, lang: str, text: str, state: TurnState, model_id: str,
                 fact_ids: list[str], extra: list[AgentWarning], citations: list | None = None) -> AgentMessage:
        # Every block the model chose is shown, even when the text repeats its values: dropping such a card left
        # only a wall of text in live tests (2026-11-11); the prompt asks the text to explain the card instead.
        blocks = state.blocks
        # The text's facts, then the ones only the presentation blocks show (validated by present_blocks).
        fact_ids = list(dict.fromkeys([*fact_ids, *(fid for b in blocks for fid in block_fact_ids(b))]))
        facts = [state.retrieved[fid] for fid in fact_ids]
        warnings: dict[str, AgentWarning] = {}

        def warn(code: str, detail: str) -> None:
            warnings.setdefault(code, AgentWarning(code=code, detail=detail))

        layers = {la.layer_id: la for la in request.field_context.available_layers}
        fixture_layers = any(layers[lid].provenance.data_status == "fixture"
                             for a in state.actions if a.action.type == "map.set_layers"
                             for lid in a.action.layer_ids if lid in layers)
        if any(f.provenance.data_status == "fixture" for f in facts) or fixture_layers:
            warn("fixture_data", "Values or layers shown are synthetic demo data.")
        if any(f.provenance.data_status == "unavailable" for f in facts) or state.missing_fact_ids:
            warn("source_unavailable", "Some requested field data is not available.")
        if state.insufficient_evidence and not citations:
            warn("source_unavailable", "The knowledge base had no passage for this question.")
        # One warning per code: replacing a confirmed condition is the one the farmer must not miss.
        if state.replaced_confirmed & set(state.constraints):
            warn("low_confidence", "A proposed condition would replace one you confirmed earlier; nothing changes "
                                   "until you confirm it.")
        if any(c.source == "agent_inferred" for c in state.constraints.values()):
            warn("low_confidence", "Some conditions were inferred, not stated; please confirm them.")
        if state.conflicting_fact_ids & set(fact_ids):
            warn("low_confidence", "The data service and the app report different values for some facts; the service's are shown.")
        if state.unverified_real_fact_ids() & set(fact_ids):
            warn("low_confidence", "Some values claim to be real measurements but were supplied by the app and not "
                                   "confirmed by a data service.")
        # A compared rotation shown to the farmer breaks a condition they confirmed (tools.constraint_conflicts).
        if state.conflicting_rotation_facts & set(fact_ids):
            warn("low_confidence", "A compared rotation does not match a condition you confirmed (an excluded crop or a "
                                   "different rotation length); the calculation did not apply your conditions.")
        if any(t.tool == "compare_rotations" and t.status == "error" for t in state.traces):
            warn("source_unavailable", "The simulation service did not produce a result for this answer.")
        if state.geo_note and state.geo_note.get("status") not in (None, "ok"):
            warn("source_unavailable", "The field data service did not provide data for this answer.")
        for w in extra:
            warnings.setdefault(w.code, w)
        return AgentMessage(
            turn_id=turn_id, locale=lang,
            blocks=[TextBlock(type="text", markdown=_trim(text)), *blocks, *_scenario_offers(state, lang)],
            facts=facts,
            citations=citations or [],
            constraints_proposed=list(state.constraints.values()),
            ui_actions=state.actions,
            warnings=list(warnings.values()),
            meta=ResponseMeta(provider=self.provider.name, model_id=model_id or "unknown",
                              prompt_version=self.prompt_version, latency_ms=0),
        )


def _trim(text: str) -> str:
    if len(text) <= MAX_MARKDOWN:
        return text
    cut = text.rfind("\n\n", 0, MAX_MARKDOWN - 1)
    return text[:cut if cut > 0 else MAX_MARKDOWN - 1].rstrip() + "…"


def _scenario_offers(state: TurnState, lang: str) -> list[ClarifyingQuestionBlock]:
    """The farmer asked for a scenario the service does not offer: the server (not the model) asks whether to
    compare another one instead. Each option names its scenario, so the reply passes the tool's scenario check."""
    done = {r.scenario for r in state.comparisons.values()}
    blocks = []
    for wanted, offered in list(state.unavailable_scenarios.items())[:1]:
        options = [scenarios.offer_text(s, lang) for s in offered if s not in done and s != wanted][:4]
        if not options:
            continue
        name = scenarios.label(wanted, lang)
        text = (f"Для варіанту «{name}» програма розрахунку поки нічого не рахує, тому результатів немає. "
                "Порівняти сівозміни для іншого варіанту? Це буде відповідь на інше питання." if lang == "uk" else
                f"The calculation program does not cover the “{name}” case yet, so there are no results for it. "
                "Compare rotations for another case instead? That answers a different question.")
        blocks.append(ClarifyingQuestionBlock(type="clarifying_question", text=text,
                                              options=[*options, "Ні, не потрібно" if lang == "uk" else "No, thanks"]))
    return blocks
