"""M6C: a simulation result for scenario A is never presented as the answer to a request for scenario B.

Provider-side mismatch (a response for another scenario) is refused at the boundary:
tests/test_trust_boundary.py::test_simulation_identity_and_scenario_must_match. The widget side is in
frontend/src/widget/actions/policy.test.ts ("another scenario").
"""

import json
from datetime import UTC, datetime, timedelta

from agent_service.integrations.fixture import FixtureSimulationProvider
from agent_service.integrations.normalize import ComparisonRecord
from agent_service.integrations.ports import UnavailableProvider
from agent_service.integrations.registry import IntegrationRegistry
from agent_service.llm.scripted import ScriptedProvider, call, demo_script, say, tool_results
from agent_service.llm_agent import LLMAgent
from agent_service.scenarios import offer_text
from agent_service.service import ui_action_problem
from contracts.integration import CapabilitiesResponse, SimulationCapabilities
from contracts.models import FieldContext, UIActionEnvelope

from .conftest import context, request_body
from .test_trust_boundary import final

DROUGHT = "Порівняй сівозміни під час посухи."
TYPICAL = offer_text("typical", "uk")  # the reply the server offers after a refusal


class DroughtCapable(FixtureSimulationProvider):
    """Test double: a service that also offers drought_2024 (values stay the fixture's, labelled synthetic)."""

    async def capabilities(self, request_id: str) -> CapabilitiesResponse:
        caps = await super().capabilities(request_id)
        return caps.model_copy(update={"simulation": SimulationCapabilities(
            compare_rotations=True, scenarios=["typical", "drought_2024"], max_rotations=6, result_ttl_s=self.ttl_s)})

    async def compare(self, request):
        wanted = request.comparison.scenario
        as_typical = request.model_copy(update={"comparison": request.comparison.model_copy(update={"scenario": "typical"})})
        return (await super().compare(as_typical)).model_copy(update={"scenario": wanted})


def ask(text: str, script=demo_script, sim=None):
    sim = sim or FixtureSimulationProvider()
    registry = IntegrationRegistry(UnavailableProvider("geo"), sim, geo_mode="off", sim_mode="fixture")
    body = request_body("poltava", text)
    body["locale"] = "uk"
    return final(LLMAgent(ScriptedProvider(script), integrations=registry), body)["message"], sim


def sim_scenarios(msg) -> set[str]:
    return {f["provenance"]["method"].split("scenario=")[1].split(";")[0] for f in msg["facts"] if f["id"].startswith("sim.")}


def offers(msg) -> list[str]:
    return [o for b in msg["blocks"] if b["type"] == "clarifying_question" for o in b["options"]]


def panels(msg) -> list[dict]:
    return [a["action"] for a in msg["ui_actions"] if a["action"]["type"] == "panel.open"]


def test_requested_drought_unavailable_is_reported_and_nothing_else_runs():
    msg, sim = ask(DROUGHT)
    assert not sim.results                                   # the service was never asked for anything
    assert not sim_scenarios(msg) and not panels(msg)
    assert "посуха" in msg["blocks"][0]["markdown"] and "не розраховано" in msg["blocks"][0]["markdown"]
    assert TYPICAL in offers(msg)                            # offered as a separate choice, not run


def test_the_farmers_explicit_choice_runs_the_alternative_labelled_as_itself():
    msg, sim = ask(TYPICAL)
    assert sim_scenarios(msg) == {"typical"} and len(sim.results) == 1
    assert [p["scenario"] for p in panels(msg)] == ["typical"]
    md = msg["blocks"][0]["markdown"]
    assert "варіант «типові умови»" in md and "«typical»" not in md  # Task 13: display name, not the id
    assert "вигадані для перевірки" in md and "посух" not in md
    assert any(w["code"] == "fixture_data" for w in msg["warnings"]) and not offers(msg)


def test_a_supported_requested_scenario_runs_and_keeps_its_name():
    msg, _ = ask(DROUGHT, sim=DroughtCapable())
    assert sim_scenarios(msg) == {"drought_2024"}
    assert [p["scenario"] for p in panels(msg)] == ["drought_2024"] and not offers(msg)
    assert "варіант «посуха (як у 2024)»" in msg["blocks"][0]["markdown"]


def test_the_model_cannot_substitute_another_scenario():
    msg, sim = ask(DROUGHT, [call(("compare_rotations", {"scenario": "typical", "rotation_ids": []})),
                             say("Порівняння сівозмін немає.")])
    assert not sim.results and not sim_scenarios(msg)
    assert TYPICAL in offers(msg)


def _say_with_score(sentence: str):
    def step(transcript, tools):
        score = next(iter(tool_results(transcript)[0]["rotations"][0]["scores"].values()))["cite_as"]
        return say(sentence.format(score=score))
    return step


def test_typical_values_cannot_be_described_as_drought_results():
    lie = _say_with_score("Під час посухи ризик дефіциту води становить {score}.")
    msg, _ = ask(TYPICAL, [call(("compare_rotations", {"scenario": "typical", "rotation_ids": []})), lie, lie])
    assert not sim_scenarios(msg)                            # the sentence and its value were removed
    assert any("scenario_mismatch" in (w.get("detail") or "") for w in msg["warnings"])


def test_naming_the_unavailable_scenario_next_to_the_shown_one_is_allowed():
    honest = _say_with_score("Посуху не розраховано; для типових умов ризик дефіциту води {score}.")
    msg, _ = ask(TYPICAL, [call(("compare_rotations", {"scenario": "typical", "rotation_ids": []})), honest])
    assert sim_scenarios(msg) == {"typical"} and not any("scenario_mismatch" in (w.get("detail") or "") for w in msg["warnings"])


def test_server_refuses_a_panel_action_naming_another_scenario():
    ctx = FieldContext.model_validate_json(json.dumps(context("poltava")))
    record = ComparisonRecord(result_id="cmp_1", field_id=ctx.field_id, field_context_revision=ctx.revision,
                              scenario="typical", rotation_ids=("rot_a",), expires_at=datetime.now(UTC) + timedelta(hours=1),
                              mode="fixture")
    env = lambda scenario: UIActionEnvelope.model_validate_json(json.dumps({  # noqa: E731
        "id": "a1", "field_context_revision": ctx.revision, "trigger": "chip", "label": "Open",
        "action": {"type": "panel.open", "panel": "comparison", "ref_id": "cmp_1", "scenario": scenario}}))
    assert ui_action_problem(env("drought_2024"), ctx, {"cmp_1": record}) == "comparison result is for another scenario"
    assert ui_action_problem(env("typical"), ctx, {"cmp_1": record}) is None


class DriestCapable(DroughtCapable):
    """Test double: a service that replays its own archive (like Marsden) and offers its driest recent year."""

    async def capabilities(self, request_id: str) -> CapabilitiesResponse:
        caps = await super().capabilities(request_id)
        return caps.model_copy(update={"simulation": caps.simulation.model_copy(
            update={"scenarios": ["typical", "driest_recent_year"]})})


def test_a_drought_question_is_answered_by_the_dry_scenario_the_service_has_without_a_spurious_offer():
    msg, sim = ask(DROUGHT, [call(("compare_rotations", {"scenario": "driest_recent_year", "rotation_ids": []})),
                             say("Порівняв сівозміни для найсухішого з недавніх років.")], sim=DriestCapable())
    assert len(sim.results) == 1 and not offers(msg)  # computed once; no "choose another scenario" question


def test_the_archive_dry_year_is_never_passed_off_as_drought_2024():
    msg, sim = ask(DROUGHT, [call(("compare_rotations", {"scenario": "drought_2024", "rotation_ids": []})),
                             say("Порівняння сівозмін немає.")], sim=DriestCapable())
    assert not sim.results and not sim_scenarios(msg)
    assert offer_text("driest_recent_year", "uk") in offers(msg)  # offered as its own, named choice
