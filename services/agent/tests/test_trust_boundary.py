"""M6A: trust boundaries — client-asserted data, service identity, result replay, demo/live separation."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from agent_service.dev_agent import DeterministicDevAgent
from agent_service.grounding import render_fact
from agent_service.integrations.fixture import FixtureGeoProvider, FixtureSimulationProvider
from agent_service.integrations.normalize import check_comparison, check_profile, idempotency_key
from agent_service.integrations.ports import IntegrationFailure, UnavailableProvider
from agent_service.integrations.registry import IntegrationRegistry
from agent_service.llm.scripted import ScriptedProvider, call, demo_script, say
from agent_service.llm_agent import LLMAgent
from contracts.integration import ComparisonResponse, FieldProfileResponse
from contracts.models import Fact, FieldContext

from .conftest import context, make_client, parse_sse, request_body
from .test_integrations import comparison_request, profile_request

FORGED = {"id": "f_sm_rootzone_now", "domain": "water", "label_key": "fact.root_zone_soil_moisture",
          "quantity": {"value": 0.48, "unit": "m3/m3"},
          "provenance": {"kind": "satellite_product", "data_status": "live",
                         "source": {"provider": "NASA NSIDC DAAC", "product_id": "SPL4SMGP", "version": "008"},
                         "spatial": {"resolution_m": 9000}, "retrieved_at": "2026-11-06T15:00:00Z"}}
LIVE_CLAIM = "Live satellite data shows root-zone moisture of {{fact:f_sm_rootzone_now}} right now."
HONEST = "The app reports root-zone moisture of {{fact:f_sm_rootzone_now}}."
READ = call(("get_field_facts", {"domains": ["water"], "fact_ids": []}))


def forged_body(text="What is my soil moisture?"):
    body = request_body("kansas", text)
    body["field_context"]["facts"].append(json.loads(json.dumps(FORGED)))
    body["locale"] = "en"
    return body


def final(agent, body):
    with make_client(agent=agent) as client:
        with client.stream("POST", "/v1/agent/stream", json=body) as resp:
            return parse_sse("".join(resp.iter_text()))[-1][2]


class LiveGeo(FixtureGeoProvider):
    """A geo service that reports the forged fact as its own live result (stands in for a real service)."""

    def __init__(self, value: float = 0.48):
        super().__init__(mode="fixture")
        self.service_mode, self.value = "live", value

    async def field_profile(self, request):
        resp = await super().field_profile(request)
        fact = Fact.model_validate_json(json.dumps(FORGED | {"quantity": {"value": self.value, "unit": "m3/m3"}}))
        return resp.model_copy(update={"profile": resp.profile.model_copy(update={"facts": [*resp.profile.facts, fact]}),
                                       "warnings": []})


def geo_registry(geo) -> IntegrationRegistry:
    return IntegrationRegistry(geo, UnavailableProvider("simulation"), geo_mode="fixture", sim_mode="off")


# ------------------------------------------------------------------ client-asserted field data


def test_forged_live_value_is_labelled_unverified_by_the_rules_agent():
    message = final(DeterministicDevAgent(), forged_body())["message"]
    line = next(line for line in message["blocks"][0]["markdown"].split("\n") if "0.48" in line)
    assert "not confirmed by the data service" in line
    assert "low_confidence" in {w["code"] for w in message["warnings"]}


def test_forged_live_status_cannot_back_a_live_claim():
    message = final(LLMAgent(ScriptedProvider([READ, say(LIVE_CLAIM), say(LIVE_CLAIM)])), forged_body())["message"]
    assert "Live satellite" not in message["blocks"][0]["markdown"]
    assert {"ungrounded_number", "low_confidence"} <= {w["code"] for w in message["warnings"]}


def test_honest_wording_keeps_the_value_with_its_unverified_label():
    message = final(LLMAgent(ScriptedProvider([READ, say(HONEST)])), forged_body())["message"]
    assert "0.48 m³/m³" in message["blocks"][0]["markdown"]
    assert "not confirmed by the data service" in message["blocks"][0]["markdown"]
    assert "low_confidence" in {w["code"] for w in message["warnings"]}


def test_the_model_is_told_which_values_are_unverified():
    provider = ScriptedProvider([READ, say(HONEST)])
    final(LLMAgent(provider), forged_body())
    facts = json.loads(provider.requests[1]["transcript"][-1].results[0].content)["facts"]
    assert {f["ref"]: f["verification"] for f in facts}["f_sm_rootzone_now"] == "app_supplied_unverified"


def test_a_value_confirmed_by_a_live_data_service_is_verified():
    agent = LLMAgent(ScriptedProvider([READ, say(LIVE_CLAIM)]), integrations=geo_registry(LiveGeo()))
    message = final(agent, forged_body())["message"]
    assert "Live satellite data shows" in message["blocks"][0]["markdown"]
    assert "not confirmed" not in message["blocks"][0]["markdown"]


def test_when_service_and_request_disagree_the_service_value_is_shown():
    agent = LLMAgent(ScriptedProvider([READ, say(HONEST)]), integrations=geo_registry(LiveGeo(value=0.19)))
    message = final(agent, forged_body())["message"]
    assert "0.19 m³/m³" in message["blocks"][0]["markdown"] and "0.48" not in message["blocks"][0]["markdown"]
    assert [f["quantity"]["value"] for f in message["facts"]] == [0.19]
    assert "low_confidence" in {w["code"] for w in message["warnings"]}


@pytest.mark.parametrize("kind_update, expected", [
    ({}, ""),  # plain satellite data: no note (its source and period are in "data used")
    ({"data_status": "fixture"}, "(demo)"),
    ({"kind": "ml_estimate", "source": None, "model": {"name": "smap-downscaling", "version": "0.1"}}, "(unchecked estimate)"),
    ({"kind": "deterministic_calc", "source": None, "method": "FAO-56 Penman-Monteith"}, "(calculated)"),
    ({"kind": "farmer_input", "source": None}, "(from you)"),
    ({"kind": "reference", "source": {"provider": "FAO", "product_id": "FAO-56", "version": "1998"}}, "reference value: FAO-56"),
])
def test_every_source_category_renders_distinguishably(kind_update, expected):
    fact = Fact.model_validate_json(json.dumps(FORGED | {"provenance": FORGED["provenance"] | kind_update}))
    rendered = render_fact(fact, "en")
    assert expected in rendered if expected else "(" not in rendered


# ------------------------------------------------------------------ simulation results


def _cmp(update) -> tuple[ComparisonResponse, object]:
    req = comparison_request()
    data = json.loads(asyncio.run(FixtureSimulationProvider().compare(req)).model_dump_json())
    update(data)
    return ComparisonResponse.model_validate_json(json.dumps(data)), req


@pytest.mark.parametrize("update, category", [
    (lambda d: d.__setitem__("scenario", "drought_2024") or d["comparison"].__setitem__("scenario", "drought_2024"), "invalid_response"),
    (lambda d: d["service"].__setitem__("service", "geo"), "invalid_response"),
])
def test_simulation_identity_and_scenario_must_match(update, category):
    resp, req = _cmp(update)
    with pytest.raises(IntegrationFailure) as e:
        check_comparison(resp, req)
    assert e.value.category == category


def test_a_geo_answer_cannot_pose_as_a_profile_from_another_service():
    resp = asyncio.run(FixtureGeoProvider().field_profile(profile_request()))
    data = json.loads(resp.model_dump_json())
    data["service"]["service"] = "simulation"
    poltava = FieldContext.model_validate_json(json.dumps(context("poltava")))
    with pytest.raises(IntegrationFailure):
        check_profile(FieldProfileResponse.model_validate_json(json.dumps(data)), poltava, "req.geo")


def test_idempotency_keys_are_scoped_to_the_caller():
    body = comparison_request().comparison.model_dump(mode="json")
    assert idempotency_key(body, "ses_a") == idempotency_key(body, "ses_a")
    assert idempotency_key(body, "ses_a") != idempotency_key(body, "ses_b")


def test_an_expired_result_is_not_replayed_by_its_idempotency_key():
    sim = FixtureSimulationProvider(ttl_s=60)
    req = comparison_request()
    first = asyncio.run(sim.compare(req))
    sim.results[first.result_id] = first.model_copy(update={
        "created_at": datetime.now(UTC) - timedelta(minutes=5), "expires_at": datetime.now(UTC) - timedelta(seconds=1)})
    again = asyncio.run(sim.compare(req))
    assert again.result_id != first.result_id and again.expires_at > datetime.now(UTC)


def test_same_session_retry_does_not_compute_twice():
    sim = FixtureSimulationProvider()
    agent = LLMAgent(ScriptedProvider(demo_script),
                     integrations=IntegrationRegistry(UnavailableProvider("geo"), sim, geo_mode="off", sim_mode="fixture"))
    for _ in range(2):  # the farmer presses "try again": a new turn, same session, same request
        final(agent, request_body("poltava", "Compare crop rotations for my field."))
    assert len(sim.results) == 1


def test_production_refuses_a_stub_or_fixture_behind_a_configured_service():
    registry = IntegrationRegistry(UnavailableProvider("geo"), FixtureSimulationProvider(), geo_mode="off",
                                   sim_mode="http", require_live=True)
    assert asyncio.run(registry.simulation_available()) is False
    assert asyncio.run(registry.status())["simulation"]["problem"] == "rejected"


def test_a_failed_call_invalidates_cached_capabilities():
    class GoesDown(FixtureSimulationProvider):
        async def compare(self, request):
            raise IntegrationFailure("unavailable", "down", retryable=True)

    registry = IntegrationRegistry(UnavailableProvider("geo"), GoesDown(), geo_mode="off", sim_mode="fixture")
    asyncio.run(registry.capabilities("simulation"))
    assert registry._probes["simulation"].checked_at > 0
    final(LLMAgent(ScriptedProvider(demo_script), integrations=registry), request_body("poltava", "Compare rotations"))
    assert registry._probes["simulation"].checked_at == float("-inf")  # "never checked": next turn probes again


def _result_id_of_a_turn(field: str, revision: int | None = None) -> str:
    sim = FixtureSimulationProvider()
    body = request_body(field, "Compare crop rotations for my field.")
    if revision is not None:
        body["field_context"]["revision"] = revision
    final(LLMAgent(ScriptedProvider(demo_script),
                   integrations=IntegrationRegistry(UnavailableProvider("geo"), sim, geo_mode="off", sim_mode="fixture")), body)
    return next(iter(sim.results))


@pytest.mark.parametrize("field, revision", [("kansas", None), ("poltava", 4)])
def test_a_result_id_from_another_turn_field_or_revision_cannot_be_referenced(field, revision):
    old = _result_id_of_a_turn("poltava", 3)
    body = request_body(field, "Open the comparison")
    if revision is not None:
        body["field_context"]["revision"] = revision
    provider = ScriptedProvider([call(("propose_ui_actions", {"actions": [
        {"type": "panel.open", "panel": "comparison", "ref_id": old, "trigger": "chip", "label": "Open", "reason": ""}]})),
        say("Here is the comparison.")])
    message = final(LLMAgent(provider, integrations=IntegrationRegistry(
        UnavailableProvider("geo"), FixtureSimulationProvider(), geo_mode="off", sim_mode="fixture")), body)["message"]
    assert message["ui_actions"] == []
    result = json.loads(provider.requests[1]["transcript"][-1].results[0].content)
    assert result["rejected"][0]["category"] == "not_available"


def test_scenario_selection_is_never_reported_as_a_completed_simulation():
    script = [call(("propose_ui_actions", {"actions": [{"type": "scenario.set", "scenario": "drought_2024",
                                                         "trigger": "auto", "label": "Drought scenario", "reason": ""}]})),
              say("I ran the simulation for the drought scenario and it shows lower yields."),
              say("I ran the simulation for the drought scenario and it shows lower yields.")]
    message = final(LLMAgent(ScriptedProvider(script)), request_body("poltava", "drought?"))["message"]
    assert "ran the simulation" not in message["blocks"][0]["markdown"]
    assert [a["trigger"] for a in message["ui_actions"]] == ["chip"]
    assert not [f for f in message["facts"] if f["domain"] == "rotation"]


def test_tool_results_never_leak_between_concurrent_sessions():
    """The idempotency scope is the session: two farmers with identical requests get separate computations."""
    sim = FixtureSimulationProvider()
    registry = IntegrationRegistry(UnavailableProvider("geo"), sim, geo_mode="off", sim_mode="fixture")
    for session in ("ses_farmer_a", "ses_farmer_b"):
        body = request_body("poltava", "Compare crop rotations for my field.", session_id=session)
        final(LLMAgent(ScriptedProvider(demo_script), integrations=registry), body)
    assert len(sim.results) == 2

