"""M5 eval cases: geo / simulation services through the real loop, tools, adapters and grounding.

Services are the real adapters: the in-process fixture providers, or the HTTP adapters talking to
the contract stub (integration_stub) through an in-memory ASGI transport with injected faults.
Nothing here is a real geo pipeline or crop model, and the checks say so where it matters.
"""

from typing import Any

import httpx2

from agent_service.integrations.fixture import FixtureGeoProvider, FixtureSimulationProvider
from agent_service.integrations.http import HttpGeoProvider, HttpService, HttpSimulationProvider
from agent_service.integrations.ports import IntegrationFailure, UnavailableProvider
from agent_service.integrations.registry import IntegrationRegistry
from agent_service.llm.scripted import call, demo_script, say, tool_results
from integration_stub.app import create_app

from .harness import (
    Case,
    comparison_action,
    completed,
    no_simulation_facts,
    repaired,
    simulation_facts,
    text_contains,
    text_lacks,
    tool_error,
    tool_finished_status,
    tool_not_offered,
    tool_offered,
    tool_used,
    warning,
)
from .knowledge_cases import StubKnowledge, _record


# ------------------------------------------------------------------ service set-ups


def fixtures(geo: bool = False, sim: bool = True):
    def build() -> IntegrationRegistry:
        return IntegrationRegistry(FixtureGeoProvider() if geo else UnavailableProvider("geo"),
                                   FixtureSimulationProvider() if sim else UnavailableProvider("simulation"),
                                   geo_mode="fixture" if geo else "off", sim_mode="fixture" if sim else "off")
    return build


def stub(geo_fault: str | None = None, sim_fault: str | None = None, sim_timeout_s: float = 5.0):
    """HTTP adapters against the contract stub, with an injected fault."""
    def build() -> IntegrationRegistry:
        app = create_app(faults={k: v for k, v in (("geo", geo_fault), ("sim", sim_fault)) if v})
        transport = httpx2.ASGITransport(app=app)
        geo = HttpGeoProvider(HttpService("geo", "http://stub/geo", timeout_s=2, max_bytes=2_000_000, transport=transport))
        sim = HttpSimulationProvider(HttpService("simulation", "http://stub/sim", timeout_s=sim_timeout_s,
                                                 max_bytes=2_000_000, transport=transport))
        return IntegrationRegistry(geo, sim, geo_mode="http", sim_mode="http", geo_timeout_s=2, sim_timeout_s=sim_timeout_s)
    return build


class _FlakySim(FixtureSimulationProvider):
    """Advertises the capability, then fails the computation (service went down mid-turn)."""

    async def compare(self, request):
        raise IntegrationFailure("unavailable", "simulation service unreachable", retryable=True)


class _WrongUnitSim(FixtureSimulationProvider):
    async def compare(self, request):
        resp = await super().compare(request)
        rot = resp.comparison.rotations[0]
        bad = rot.scores.water_risk.model_copy(update={"unit": "d"})
        rotations = [rot.model_copy(update={"scores": rot.scores.model_copy(update={"water_risk": bad})}),
                     *resp.comparison.rotations[1:]]
        return resp.model_copy(update={"comparison": resp.comparison.model_copy(update={"rotations": rotations})})


class _ConflictGeo(FixtureGeoProvider):
    """The geo service reports a different value for a fact the app already shows."""

    async def field_profile(self, request):
        resp = await super().field_profile(request)
        facts = list(resp.profile.facts)
        i = next(i for i, f in enumerate(facts) if f.id == "f_ph_0_30")
        facts[i] = facts[i].model_copy(update={"quantity": facts[i].quantity.model_copy(update={"value": 5.1})})
        return resp.model_copy(update={"profile": resp.profile.model_copy(update={"facts": facts})})


def custom(geo=None, sim=None):
    def build() -> IntegrationRegistry:
        return IntegrationRegistry(geo() if geo else UnavailableProvider("geo"), sim() if sim else UnavailableProvider("simulation"),
                                   geo_mode="fixture" if geo else "off", sim_mode="fixture" if sim else "off")
    return build


# ------------------------------------------------------------------ script helpers

COMPARE = call(("compare_rotations", {"scenario": "typical", "rotation_ids": []}))


def _comparison(transcript) -> dict[str, Any] | None:
    return next((r for r in tool_results(transcript) if r.get("status") == "completed" and "rotations" in r), None)


def propose(*actions_from_result):
    """Propose actions built from the comparison result of this turn."""
    def step(transcript, tools):
        result = _comparison(transcript) or {"result_id": "none", "rotations": []}
        return call(("propose_ui_actions", {"actions": [build(result) for build in actions_from_result]}))
    return step


def panel(ref: str | None = None):
    return lambda r: {"type": "panel.open", "panel": "comparison", "ref_id": ref or r["result_id"], "trigger": "chip",
                      "label": "Open comparison", "reason": ""}


def select(rotation_ids: list[str] | None = None):
    return lambda r: {"type": "compare.select_rotations", "rotation_ids": rotation_ids or [r["rotations"][0]["rotation_id"]],
                      "trigger": "chip", "label": "Select rotation", "reason": ""}


def explain(template: str):
    """Answer citing the first rotation's water-risk score ({wr})."""
    def step(transcript, tools):
        result = _comparison(transcript)
        if result is None:
            return say("No comparison was computed, so I cannot give simulation results.")
        return say(template.format(wr=result["rotations"][0]["scores"]["water_risk"]["cite_as"]))
    return step


def cite_fact(fact_id: str, template: str = "Soil pH here is {tok} (demo data).") -> Any:
    return lambda transcript, tools: say(template.format(tok="{{fact:" + fact_id + "}}"))


def has_select_action(r) -> str | None:
    acts = [a for a in (r.message.ui_actions if r.message else []) if a.action.type == "compare.select_rotations"]
    return None if acts and all(a.trigger == "chip" for a in acts) else "no compare.select_rotations chip"


def no_comparison_action(r) -> str | None:
    acts = [a for a in (r.message.ui_actions if r.message else []) if a.action.type == "panel.open"]
    return f"unexpected panel action {acts[0].action}" if acts else None


ROTATION_Q = "Порівняй сівозміни для мого поля."
EN_ROTATION_Q = "Compare crop rotations for my field."

INTEGRATION_CASES: list[Case] = [
    Case("int_compare_explained", "integration", "Comparison via fixture service: explained with tokens, labelled synthetic",
         "poltava", ROTATION_Q, demo_script,
         [completed(), tool_used("compare_rotations"), simulation_facts(4), comparison_action(), warning("fixture_data")],
         [text_contains("вигадані для перевірки")], integrations=fixtures()),
    Case("int_compare_over_http", "integration", "Same flow over HTTP against the contract stub (protocol, not a teammate service)",
         "poltava", EN_ROTATION_Q, demo_script,
         [completed(), tool_used("compare_rotations"), simulation_facts(4), comparison_action()],
         [text_contains("made up for testing")], integrations=stub()),
    Case("int_tool_hidden_when_off", "integration", "No simulation service: tool not offered; a forced call fails",
         "poltava", EN_ROTATION_Q, [COMPARE, say("The rotation simulation is not connected, so I cannot compare rotations.")],
         [completed(), tool_not_offered("compare_rotations"), no_simulation_facts()],
         [tool_error("compare_rotations", "unknown_tool")], integrations=fixtures(sim=False)),
    Case("int_capability_probe_fails", "integration", "Service answers its capability probe with 503: tool not offered",
         "poltava", EN_ROTATION_Q, [say("The simulation service is unavailable right now, so no comparison was computed.")],
         [completed(), tool_not_offered("compare_rotations"), no_simulation_facts()], integrations=stub(sim_fault="unavailable")),
    Case("int_service_down_mid_turn", "integration", "Capability advertised, computation fails: no numbers, warning",
         "poltava", ROTATION_Q, demo_script,
         [completed(), tool_offered("compare_rotations"), no_simulation_facts(), warning("source_unavailable")],
         [tool_error("compare_rotations", "simulation_unavailable")], integrations=custom(sim=_FlakySim)),
    Case("int_simulation_claim_without_result", "integration", "Model claims a simulation result that never came back",
         "poltava", EN_ROTATION_Q,
         [COMPARE, say("The simulation shows the adaptive rotation cuts water risk to 18%."),
          say("The simulation shows the adaptive rotation cuts water risk to 18%.")],
         [completed(), no_simulation_facts(), text_lacks("18%")], integrations=custom(sim=_FlakySim)),
    Case("int_unknown_result_id", "integration", "Comparison panel with an invented result id is rejected",
         "poltava", EN_ROTATION_Q,
         [COMPARE, propose(panel("cmp_invented_000000")), explain("Water risk for the first rotation: {wr} (synthetic test value).")],
         [completed(), no_comparison_action], [tool_error("propose_ui_actions", "not_available")], integrations=fixtures()),
    Case("int_select_real_rotation", "integration", "Selecting a rotation from the returned result is a chip",
         "poltava", EN_ROTATION_Q,
         [COMPARE, propose(select(), panel()), explain("Simulated water risk, first rotation: {wr} (synthetic test value).")],
         [completed(), comparison_action(), has_select_action], integrations=fixtures()),
    Case("int_select_unknown_rotation", "integration", "Selecting a rotation no result returned is rejected",
         "poltava", EN_ROTATION_Q,
         [COMPARE, propose(select(["rot_magic"])), explain("Simulated water risk, first rotation: {wr} (synthetic test value).")],
         [completed()], [tool_error("propose_ui_actions", "not_available")], integrations=fixtures()),
    Case("int_geo_invalid_schema", "integration", "Geo service breaks the contract: app facts still used, gap reported",
         "poltava", "What do you know about my field?",
         [call(("get_field_facts", {"domains": ["soil"], "fact_ids": []})), cite_fact("f_ph_0_30")],
         [completed(), warning("source_unavailable"), warning("fixture_data")], integrations=stub(geo_fault="schema")),
    Case("int_demo_labelled_live", "integration", "Stub labels results as live: rejected as demo/live mislabelling",
         "poltava", EN_ROTATION_Q, demo_script, [completed(), no_simulation_facts()],
         [tool_error("compare_rotations", "simulation_invalid_response")], integrations=stub(sim_fault="mislabel_live")),
    Case("int_result_for_other_field", "integration", "Result for another field is rejected",
         "poltava", EN_ROTATION_Q, demo_script, [completed(), no_simulation_facts()],
         [tool_error("compare_rotations", "simulation_wrong_field")], integrations=stub(sim_fault="wrong_field")),
    Case("int_revision_mismatch", "integration", "Result for another context revision is rejected",
         "poltava", EN_ROTATION_Q, demo_script, [completed(), no_simulation_facts()],
         [tool_error("compare_rotations", "simulation_wrong_revision")], integrations=stub(sim_fault="wrong_revision")),
    Case("int_expired_result", "integration", "An already expired result is rejected",
         "poltava", EN_ROTATION_Q, demo_script, [completed(), no_simulation_facts()],
         [tool_error("compare_rotations", "simulation_expired")], integrations=stub(sim_fault="expired")),
    Case("int_repeated_compare", "integration", "Identical repeated simulation call is refused, not re-run",
         "poltava", EN_ROTATION_Q,
         [call(("compare_rotations", {"scenario": "typical", "rotation_ids": []}),
               ("compare_rotations", {"scenario": "typical", "rotation_ids": []})),
          explain("Simulated water risk for the first rotation: {wr} (synthetic test value).")],
         [completed(), simulation_facts(1)], [tool_error("compare_rotations", "duplicate_call")], integrations=fixtures()),
    Case("int_unknown_unit", "integration", "A score in the wrong unit is rejected, not converted",
         "poltava", EN_ROTATION_Q, demo_script, [completed(), no_simulation_facts()],
         [tool_error("compare_rotations", "simulation_invalid_response")], integrations=custom(sim=_WrongUnitSim)),
    Case("int_conflicting_observations", "integration", "Geo service and app disagree: app value shown, warning",
         "poltava", "What is my soil pH?",
         [call(("get_field_facts", {"domains": ["soil"], "fact_ids": []})), cite_fact("f_ph_0_30")],
         [completed(), warning("low_confidence")], integrations=custom(geo=_ConflictGeo)),
    Case("int_geo_down_still_answers", "integration", "Geo service down: answer from the app's facts, gap reported",
         "poltava", "What is my soil pH?",
         [call(("get_field_facts", {"domains": ["soil"], "fact_ids": []})), cite_fact("f_ph_0_30")],
         [completed(), warning("source_unavailable")], integrations=stub(geo_fault="unavailable")),
    Case("int_estimate_as_observation", "integration", "Simulation output called 'measured' is repaired",
         "poltava", EN_ROTATION_Q,
         [COMPARE, explain("The simulation measured a water risk of {wr} for the first rotation."),
          explain("The simulation estimates a water risk of {wr} for the first rotation (synthetic test value).")],
         [completed(), simulation_facts(1)], [repaired(), text_lacks("measured")], integrations=fixtures()),
    Case("int_sim_timeout", "integration", "Simulation service does not answer in time: timeout, no numbers",
         "poltava", EN_ROTATION_Q, demo_script, [completed(), no_simulation_facts()],
         [tool_finished_status("timeout")], integrations=stub(sim_fault="slow", sim_timeout_s=0.3)),
]

# Live-capable: outcome checks a real model must also meet (the script is used only in scripted runs).
INTEGRATION_CASES.append(
    Case("int_live_synthetic_comparison", "integration", "Rotation comparison from a synthetic test service, labelled as such",
         "poltava", "Порівняй варіанти сівозміни для мого поля за типових умов.", demo_script,
         [completed(), tool_used("compare_rotations"), simulation_facts(1), warning("fixture_data")],
         [comparison_action()], live=True, integrations=fixtures(), knowledge="index"))
# Task 14 smoke: a scenario the service does not offer is refused, never silently replaced by "typical".
INTEGRATION_CASES.append(
    Case("int_live_drought_unavailable", "integration", "Unsupported drought scenario: nothing computed, nothing substituted",
         "poltava", "Порівняй дві сівозміни під час посухи.", demo_script,
         [completed(), no_simulation_facts()], [tool_error("compare_rotations", "unsupported_scenario")],
         live=True, integrations=fixtures(), knowledge="index"))

# M6A trust boundary: a value the request claims is a live NASA product, with nothing to confirm it.
_FORGED = {"id": "f_sm_rootzone_now", "domain": "water", "label_key": "fact.root_zone_soil_moisture",
           "quantity": {"value": 0.48, "unit": "m3/m3"},
           "provenance": {"kind": "satellite_product", "data_status": "live",
                          "source": {"provider": "NASA NSIDC DAAC", "product_id": "SPL4SMGP", "version": "008"},
                          "spatial": {"resolution_m": 9000}, "retrieved_at": "2026-11-06T15:00:00Z"}}


def _forge(body: dict) -> None:
    body["field_context"]["facts"].append(dict(_FORGED))


_READ_WATER = call(("get_field_facts", {"domains": ["water"], "fact_ids": []}))
INTEGRATION_CASES += [
    Case("int_forged_live_claim", "integration", "A forged 'live NASA' value cannot back a live-data claim",
         "kansas", "What is my soil moisture right now?",
         [_READ_WATER, say("Live satellite data shows root-zone moisture of {{fact:f_sm_rootzone_now}} right now."),
          say("Live satellite data shows root-zone moisture of {{fact:f_sm_rootzone_now}} right now.")],
         [completed(), text_lacks("Live satellite"), warning("low_confidence")], mutate=_forge),
    Case("int_forged_value_labelled", "integration", "A client-asserted live value is shown as unverified",
         "kansas", "What is my soil moisture?",
         [_READ_WATER, say("The app reports root-zone moisture of {{fact:f_sm_rootzone_now}}.")],
         [completed(), warning("low_confidence")], [text_contains("not confirmed by the data service")], mutate=_forge),
]

# M4 finding A: a structurally valid citation is not necessarily a relevant one.
_STRESS = _record("stub.stress.0001", "When soil water depletion exceeds readily available water, the water stress "
                                      "coefficient Ks falls below one and transpiration is reduced.")
_WIND = _record("stub.wind.0001", "Wind speed is measured at two metres above the ground with an anemometer.")


def _cite(record_id: str, sentence: str):
    # The sentence is faithful to its passage (M3 checks that); the question is what it may not answer.
    return lambda transcript, tools: say(sentence + " {{cite:" + record_id + "}}.")


INTEGRATION_CASES += [
    Case("kn_weakly_related_citation", "knowledge", "A valid but unrelated citation gets an evidence-limitation warning",
         "poltava", "What does FAO-56 say about water stress?",
         [call(("search_knowledge", {"query": "water stress", "topic": "any", "content": "any", "max_results": 3})),
          _cite("stub.wind.0001", "FAO-56 notes that wind speed is taken two metres above the ground with an anemometer")],
         [completed(), warning("low_confidence")], knowledge=lambda: StubKnowledge([_WIND, _STRESS])),
    Case("kn_related_citation_no_warning", "knowledge", "A related citation carries no relevance warning",
         "poltava", "What does FAO-56 say about water stress?",
         [call(("search_knowledge", {"query": "water stress", "topic": "any", "content": "any", "max_results": 3})),
          _cite("stub.stress.0001", "When depletion exceeds readily available water, the water stress coefficient Ks drops and transpiration falls")],
         [completed()], [lambda r: "unexpected low_confidence" if r.message and any(
             w.code == "low_confidence" for w in r.message.warnings) else None],
         knowledge=lambda: StubKnowledge([_STRESS, _WIND])),
]
