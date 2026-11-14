"""Eval harness: run a case through the real orchestration path and check its outcome.

Path under test: AgentService -> LLMAgent -> tool registry -> grounding -> M1 output checks.
Only the model is replaced (ScriptedProvider). Checks look at outcomes (events, message,
what the model was sent), never at internal functions, so they cannot simply mirror the code.
"""

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from agent_service.agent import TurnEvidence
from agent_service.llm.provider import AssistantTurn, LLMProvider, ToolResults, UserText
from agent_service.llm.scripted import ScriptedProvider
from agent_service.llm_agent import LLMAgent, LoopLimits
from agent_service.service import AgentService
from contracts.export import SCHEMA_PATH
from contracts.models import AgentRequest

FIXTURES = Path(__file__).parents[1] / "contracts" / "fixtures"
_BUNDLE = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
_EVENT_SCHEMA = jsonschema.Draft202012Validator({"$ref": "#/$defs/AgentEvent", "$defs": _BUNDLE["$defs"]})

Check = Callable[["Run"], str | None]   # returns a failure reason, or None


@dataclass
class Case:
    id: str
    category: str          # tool_selection | constraints | data_reliability | security | provider
    title: str
    field: str             # fixture name: poltava | kansas | mykolaiv
    text: str | None
    script: list[Any] | Callable[..., Any]
    expect: list[Check]                                   # outcome checks, valid for a real model too
    scripted_expect: list[Check] = field(default_factory=list)  # checks that depend on the script
    live: bool = False                                    # part of the bounded live-model run
    mutate: Callable[[dict], None] | None = None          # edit the field context / request
    limits: LoopLimits | None = None
    attachments: bool = False
    knowledge: Any = None    # None | "index" (real FAO-56 index, lexical) | a stub knowledge base object
    integrations: Any = None  # None | a factory returning an IntegrationRegistry (geo / simulation services)


@dataclass
class Run:
    case: Case
    request: AgentRequest
    events: list[Any]
    provider: LLMProvider
    knowledge: Any = None
    evidence: TurnEvidence = field(default_factory=TurnEvidence)

    @property
    def terminal(self):
        return self.events[-1] if self.events else None

    @property
    def message(self):
        return self.terminal.message if self.terminal is not None and self.terminal.event == "turn.completed" else None

    @property
    def error(self):
        return self.terminal if self.terminal is not None and self.terminal.event == "error" else None

    @property
    def text(self) -> str:
        return self.message.blocks[0].markdown if self.message else ""

    def tool_results(self) -> list[tuple[str, dict, bool]]:
        """(tool name, decoded result, is_error) for every result the model was sent."""
        requests = getattr(self.provider, "requests", [])
        if not requests:
            return []
        transcript = requests[-1]["transcript"]
        names = {c.id: c.name for item in transcript if isinstance(item, AssistantTurn) for c in item.response.tool_calls}
        return [(names.get(r.call_id, "?"), json.loads(r.content), r.is_error)
                for item in transcript if isinstance(item, ToolResults) for r in item.results]

    def first_user_message(self) -> str:
        requests = getattr(self.provider, "requests", [])
        return next((i.text for i in requests[0]["transcript"] if isinstance(i, UserText)), "") if requests else ""


def build_request(case: Case) -> AgentRequest:
    ctx = json.loads((FIXTURES / f"field_context.{case.field}.json").read_text(encoding="utf-8"))
    body: dict[str, Any] = {
        "request_id": f"eval_{case.id}"[:128], "session_id": "eval", "locale": "auto",
        "message": {"text": case.text} if case.text is not None else {},
        "history": [], "field_context": copy.deepcopy(ctx), "client": {"widget_version": "eval"},
    }
    if case.attachments:
        body["message"]["attachments"] = [{"attachment_id": "img1", "kind": "image"}]
    if case.mutate:
        case.mutate(body)
    return AgentRequest.model_validate_json(json.dumps(body, ensure_ascii=False))


def invariant_failures(run: Run) -> list[tuple[str, str]]:
    """Contract invariants every case must hold, whatever it tests."""
    failures = []
    for i, event in enumerate(run.events):
        errors = list(_EVENT_SCHEMA.iter_errors(event.model_dump(mode="json")))
        if errors:
            failures.append(("contract.event_schema", f"event {i} ({event.event}): {errors[0].message[:120]}"))
    names = [e.event for e in run.events]
    if not names or names[0] != "turn.started":
        failures.append(("contract.order", "first event is not turn.started"))
    if names.count("turn.completed") + names.count("error") != 1 or names[-1] not in ("turn.completed", "error"):
        failures.append(("contract.terminal", f"terminal events: {names}"))
    if run.message:
        deltas = "".join(e.text for e in run.events if e.event == "text.delta")
        if deltas != run.text:
            failures.append(("contract.deltas", "text deltas do not rebuild the final text"))
        known = {f.id: f for f in run.request.field_context.facts} | {f.id: f for f in run.evidence.facts}
        if any(known.get(f.id) != f for f in run.message.facts):
            failures.append(("grounding.facts_verbatim", "message fact is not from the context or a verified tool result"))
        results = {r.result_id: r for r in run.evidence.comparisons}
        for a in run.message.ui_actions:
            act = a.action
            if act.type == "panel.open" and act.panel == "comparison" and act.ref_id and act.ref_id not in results:
                failures.append(("safety.unknown_result", f"action {a.id} references result {act.ref_id}"))
            if act.type == "compare.select_rotations" and not set(act.rotation_ids) <= {
                    rid for r in results.values() for rid in r.rotation_ids}:
                failures.append(("safety.unknown_rotation", f"action {a.id} selects rotations no result returned"))
        for f in run.message.facts:
            if f.id.startswith("sim.") and f.provenance.kind not in ("deterministic_calc", "ml_estimate"):
                failures.append(("provenance.simulation_kind", f"simulation fact {f.id} is not marked as computed"))
            if f.id.startswith("sim.") and any(r.mode != "live" for r in results.values()) and f.provenance.data_status != "fixture":
                failures.append(("provenance.demo_as_live", f"{f.id} from a test service is not labelled fixture"))
        layers = {la.layer_id for la in run.request.field_context.available_layers}
        for a in run.message.ui_actions:
            if a.field_context_revision != run.request.field_context.revision:
                failures.append(("safety.revision", f"action {a.id} has a stale revision"))
            if a.action.type == "map.set_layers" and not set(a.action.layer_ids) <= layers:
                failures.append(("safety.unknown_layer", f"action {a.id} references an unknown layer"))
    elif run.error is None:
        failures.append(("contract.terminal", "no terminal event"))
    if run.error is not None and any(e.event == "text.delta" for e in run.events):
        failures.append(("grounding.unvalidated_text", "text was streamed in a turn that failed"))
    return failures


INDEX_PATH = Path(__file__).parents[1] / "data" / "knowledge" / "index" / "knowledge.sqlite"
_KB: Any = None


class KnowledgeMissing(Exception):
    pass


def knowledge_base():
    """The real FAO-56 index, loaded once and searched lexically (deterministic, no models)."""
    global _KB
    if _KB is None:
        from agent_service.knowledge.retrieve import KnowledgeBase, KnowledgeUnavailable

        try:
            _KB = KnowledgeBase(INDEX_PATH, mode="lexical", load_models=False)
        except KnowledgeUnavailable as e:
            raise KnowledgeMissing(str(e)) from e
    return _KB


class _EvidenceTap:
    """Wraps the agent to record the TurnEvidence the service consumes (it is never streamed)."""

    def __init__(self, agent: LLMAgent):
        self.agent, self.name, self.evidence = agent, agent.name, TurnEvidence()

    async def respond(self, request, turn_id):
        async for item in self.agent.respond(request, turn_id):
            if isinstance(item, TurnEvidence):
                self.evidence = item
            yield item


async def run_case(case: Case, provider: LLMProvider | None = None, live: bool = False,
                   limits: LoopLimits | None = None) -> tuple[Run, list[tuple[str, str]]]:
    request = build_request(case)
    provider = provider or ScriptedProvider(case.script)
    kb = knowledge_base() if case.knowledge == "index" else (case.knowledge() if callable(case.knowledge) else case.knowledge)
    integrations = case.integrations() if callable(case.integrations) else case.integrations
    agent = LLMAgent(provider, limits or case.limits or LoopLimits(), knowledge=kb, knowledge_timeout_s=(case.limits.tool_timeout_s
                                                                                              if case.limits else 20.0),
                     integrations=integrations)
    tap = _EvidenceTap(agent)
    try:
        events = [e async for e in AgentService(tap, turn_timeout_s=120 if live else 10).stream(request)]
    finally:
        if integrations is not None:
            await integrations.aclose()
    run = Run(case, request, events, provider, kb, tap.evidence)
    failures = invariant_failures(run)
    for check in case.expect + ([] if live else case.scripted_expect):
        if reason := check(run):
            failures.append((getattr(check, "check_name", "check"), reason))
    return run, failures


# ------------------------------------------------------------------ check library


def _named(name: str, fn: Check) -> Check:
    fn.check_name = name  # type: ignore[attr-defined]
    return fn


def completed() -> Check:
    return _named("outcome.completed", lambda r: None if r.message else f"expected a message, got {r.terminal and r.terminal.event}"
                  + (f" ({r.error.code}/{r.error.message_key})" if r.error else ""))


def failed(code: str, retryable: bool | None = None) -> Check:
    def check(r: Run) -> str | None:
        if not r.error:
            return "expected an error event"
        if r.error.code != code:
            return f"error code {r.error.code}, expected {code}"
        if retryable is not None and r.error.retryable != retryable:
            return f"retryable={r.error.retryable}, expected {retryable}"
        return None
    return _named("outcome.failed", check)


def constraints(*expected: tuple[str, Any]) -> Check:
    def check(r: Run) -> str | None:
        got = {(c.type, c.value if not isinstance(c.value, list) else tuple(c.value)) for c in (r.message.constraints_proposed if r.message else [])}
        return None if got == set(expected) else f"constraints {sorted(got, key=str)}, expected {sorted(expected, key=str)}"
    return _named("constraints.set", check)


def has_constraint(type_: str, value: Any = ..., source: str | None = None) -> Check:
    def check(r: Run) -> str | None:
        for c in (r.message.constraints_proposed if r.message else []):
            if c.type == type_ and (value is ... or c.value == value) and (source is None or c.source == source):
                return None
        return f"no {type_}={value} constraint" + (f" from {source}" if source else "")
    return _named("constraints.has", check)


def no_constraint(type_: str) -> Check:
    return _named("constraints.absent", lambda r: f"unexpected {type_} constraint" if r.message and any(
        c.type == type_ for c in r.message.constraints_proposed) else None)


def action(type_: str, *, layer: str | None = None, trigger: str | None = None, scenario: str | None = None) -> Check:
    def check(r: Run) -> str | None:
        for a in (r.message.ui_actions if r.message else []):
            if a.action.type != type_ or (trigger and a.trigger != trigger):
                continue
            if layer and layer not in getattr(a.action, "layer_ids", []):
                continue
            if scenario and getattr(a.action, "scenario", None) != scenario:
                continue
            return None
        return f"no {type_} action" + (f" for {layer}" if layer else "") + (f" with trigger {trigger}" if trigger else "")
    return _named("actions.has", check)


def no_action(type_: str | None = None) -> Check:
    return _named("actions.absent", lambda r: "unexpected UI action" if r.message and any(
        type_ is None or a.action.type == type_ for a in r.message.ui_actions) else None)


def facts_include(*ids: str) -> Check:
    return _named("facts.include", lambda r: None if r.message and set(ids) <= {f.id for f in r.message.facts}
                  else f"facts {[f.id for f in r.message.facts] if r.message else None} lack {ids}")


def no_facts(domain: str | None = None, label_part: str | None = None) -> Check:
    def check(r: Run) -> str | None:
        bad = [f.id for f in (r.message.facts if r.message else [])
               if (domain is None or f.domain == domain) and (label_part is None or label_part in f.label_key)]
        return f"unexpected facts {bad}" if bad else None
    return _named("facts.absent", check)


def text_contains(*parts: str) -> Check:
    return _named("text.contains", lambda r: next((f"text lacks {p!r}" for p in parts if p not in r.text), None))


def text_lacks(*parts: str) -> Check:
    return _named("text.lacks", lambda r: next((f"text contains {p!r}" for p in parts if p in r.text), None))


def warning(code: str) -> Check:
    return _named("warnings.has", lambda r: None if r.message and code in {w.code for w in r.message.warnings}
                  else f"no {code} warning")


def no_warning(code: str) -> Check:
    return _named("warnings.absent", lambda r: f"unexpected {code} warning" if r.message and code in {
        w.code for w in r.message.warnings} else None)


def tool_used(name: str) -> Check:
    return _named("tools.used", lambda r: None if any(getattr(e, "tool", None) == name for e in r.events)
                  else f"{name} was not called")


def no_tool_event(name: str) -> Check:
    return _named("tools.no_event", lambda r: f"event for {name}" if any(getattr(e, "tool", None) == name for e in r.events) else None)


def tool_error(name: str, category: str) -> Check:
    def check(r: Run) -> str | None:
        for tool, result, is_error in r.tool_results():
            if tool != name:
                continue
            if is_error and result.get("error", {}).get("category") == category:
                return None
            if any(x.get("category") == category for x in result.get("rejected", [])):
                return None
        return f"no {category} result from {name}"
    return _named("tools.error_category", check)


def tool_result_has(name: str, key: str) -> Check:
    def check(r: Run) -> str | None:
        for tool, result, _ in r.tool_results():
            if tool == name and any(key in a for a in result.get("accepted", [])):
                return None
        return f"no accepted {name} entry with {key!r}"
    return _named("tools.result_key", check)


def tool_not_offered(name: str) -> Check:
    def check(r: Run) -> str | None:
        offered = {t.name for req in getattr(r.provider, "requests", []) for t in req["tools"]}
        return f"{name} was offered to the model" if name in offered else None
    return _named("tools.not_offered", check)


def tool_offered(name: str) -> Check:
    def check(r: Run) -> str | None:
        offered = {t.name for req in getattr(r.provider, "requests", []) for t in req["tools"]}
        return None if name in offered else f"{name} was not offered to the model"
    return _named("tools.offered", check)


def simulation_facts(minimum: int, data_status: str = "fixture") -> Check:
    def check(r: Run) -> str | None:
        facts = [f for f in (r.message.facts if r.message else []) if f.id.startswith("sim.")]
        if len(facts) < minimum:
            return f"{len(facts)} simulation facts, expected at least {minimum}"
        if bad := [f.id for f in facts if f.provenance.data_status != data_status]:
            return f"simulation facts not labelled {data_status}: {bad[:2]}"
        return None
    return _named("simulation.facts", check)


def no_simulation_facts() -> Check:
    return _named("simulation.no_facts", lambda r: "simulation facts present" if r.message and any(
        f.id.startswith("sim.") or f.domain == "rotation" for f in r.message.facts) else None)


def comparison_action() -> Check:
    def check(r: Run) -> str | None:
        refs = [a.action.ref_id for a in (r.message.ui_actions if r.message else [])
                if a.action.type == "panel.open" and a.action.panel == "comparison"]
        known = {c.result_id for c in r.evidence.comparisons}
        return None if refs and set(refs) <= known else f"no comparison action referencing a verified result ({refs})"
    return _named("actions.comparison_ref", check)


def repaired() -> Check:
    return _named("grounding.repair_round", lambda r: None if any(
        "<grounding_check>" in i.text for req in getattr(r.provider, "requests", [])
        for i in req["transcript"] if isinstance(i, UserText)) else "no grounding repair round happened")


def tool_finished_status(status: str) -> Check:
    return _named("tools.finished_status", lambda r: None if any(
        e.event == "tool.finished" and e.status == status for e in r.events) else f"no tool.finished with status {status}")


def no_tool_finished_status(status: str) -> Check:
    """The farmer's progress list never shows a step with this status."""
    return _named("tools.no_finished_status", lambda r: f"a tool.finished with status {status}" if any(
        e.event == "tool.finished" and e.status == status for e in r.events) else None)


def no_text_deltas() -> Check:
    return _named("stream.no_text", lambda r: "text was streamed" if any(e.event == "text.delta" for e in r.events) else None)


def prompt_lacks(*parts: str) -> Check:
    def check(r: Run) -> str | None:
        for req in getattr(r.provider, "requests", []):
            blob = req["system"] + json.dumps([getattr(i, "text", "") or str(getattr(i, "results", ""))
                                               for i in req["transcript"]], ensure_ascii=False)
            for p in parts:
                if p in blob:
                    return f"model input contains {p!r}"
        return None
    return _named("security.prompt_lacks", check)


def citations_count(n: int) -> Check:
    return _named("citations.count", lambda r: None if r.message and len(r.message.citations) == n
                  else f"{len(r.message.citations) if r.message else None} citations, expected {n}")


def citation_where(predicate, description: str) -> Check:
    return _named("citations.match", lambda r: None if r.message and any(predicate(c) for c in r.message.citations)
                  else f"no citation where {description}")


def no_citation_tokens() -> Check:
    return _named("citations.rendered", lambda r: "a raw citation token reached the farmer" if "{{" in r.text else None)
