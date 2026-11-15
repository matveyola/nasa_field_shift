"""Strategy comparisons grounded in evidence (Task 06A-1, 2026-11-15).

Deterministic, scripted model. What the server guarantees whatever a model writes: a compared rotation that breaks a
farmer-confirmed rule is flagged (and shown with a warning); the result never says the farmer's conditions were
applied (no connected service applies them); the answer cannot claim they were; without a simulation nothing is
invented. How a real model words its trade-offs is a live question (evals/strategy_cases.py, `live=True`).
"""

import json
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter

from agent_service.integrations.fixture import FixtureSimulationProvider
from agent_service.integrations.ports import UnavailableProvider
from agent_service.integrations.registry import IntegrationRegistry
from agent_service.llm.scripted import ScriptedProvider, call, demo_script, say, tool_results
from agent_service.llm_agent import LLMAgent
from agent_service.prompts import SYSTEM_PROMPT, user_message
from agent_service.tools import constraint_conflicts
from contracts.models import Constraint

from .conftest import as_request, request_body
from .test_trust_boundary import final

COMPARE = ("compare_rotations", {"scenario": "typical", "rotation_ids": []})
NO_SUNFLOWER = {"id": "pc_excluded_crops", "type": "excluded_crops", "value": ["sunflower"], "status": "confirmed",
                "source": "farmer_ui"}
AT_MOST_TWO_YEARS = {"id": "pc_rotation_length_years", "type": "rotation_length_years", "value": 2,
                     "status": "confirmed", "source": "farmer_ui"}


def registry(sim: bool = True) -> IntegrationRegistry:
    return IntegrationRegistry(UnavailableProvider("geo"), FixtureSimulationProvider() if sim else UnavailableProvider(
        "simulation"), geo_mode="off", sim_mode="fixture" if sim else "off")


def body(text: str, *constraints: dict, lang: str = "uk") -> dict:
    b = request_body("poltava", text)  # poltava: no irrigation (confirmed), less nitrogen (proposed)
    b["locale"] = lang
    b["field_context"]["constraints"] += list(constraints)
    return b


def turn(text: str, script, *constraints: dict, sim: bool = True, lang: str = "uk") -> dict:
    agent = LLMAgent(ScriptedProvider(script), integrations=registry(sim) if sim else None)
    return final(agent, body(text, *constraints, lang=lang))["message"]


def cite_rotation(rotation_id: str, sentence: str):
    """A scripted model that compares rotations, then cites one rotation's first score in `sentence` ({ref})."""
    seen: dict = {}

    def script(transcript, tools):
        results = tool_results(transcript)
        if not results:
            return call(COMPARE)
        seen["result"] = results[-1]
        rotation = next(r for r in results[-1]["rotations"] if r["rotation_id"] == rotation_id)
        return say(sentence.format(ref=next(iter(rotation["scores"].values()))["cite_as"]))
    return script, seen


# ------------------------------------------------------------------ the comparison result


def test_a_rotation_breaking_a_confirmed_rule_is_flagged_and_conditions_are_never_called_applied():
    script, seen = cite_rotation("rot_adaptive", "У типових умовах ризик нестачі води {ref}; це розрахунок.")
    turn("Порівняй сівозміни.", script, NO_SUNFLOWER, AT_MOST_TWO_YEARS)
    result = seen["result"]
    assert "constraints_used" not in result
    assert {"pc_excluded_crops", "pc_rotation_length_years"} <= set(result["constraints_sent"])
    assert "never say these results take them into account" in result["constraints_note"]
    by_id = {r["rotation_id"]: r["farmer_constraint_conflicts"] for r in result["rotations"]}
    assert {"constraint_id": "pc_excluded_crops", "type": "excluded_crops", "crops": ["sunflower"]} in \
        by_id["rot_conventional"]
    assert all(c["type"] != "excluded_crops" for c in by_id["rot_adaptive"])
    for conflicts in by_id.values():  # both fixture rotations last three years
        assert {"constraint_id": "pc_rotation_length_years", "type": "rotation_length_years", "years": 3,
                "confirmed_years": 2} in conflicts


def test_rejected_and_proposed_conditions_never_count_as_rules():
    rejected = {**NO_SUNFLOWER, "status": "rejected"}
    proposed = {**AT_MOST_TWO_YEARS, "id": "k_rotation_length_years", "status": "proposed", "source": "farmer_text",
                "evidence": "не більше двох років"}
    script, seen = cite_rotation("rot_conventional", "Ризик нестачі води {ref}; це розрахунок, не врожай.")
    message = turn("Порівняй сівозміни.", script, rejected, proposed)
    assert all(r["farmer_constraint_conflicts"] == [] for r in seen["result"]["rotations"])
    assert not any("did not apply your conditions" in (w.get("detail") or "") for w in message["warnings"])


def test_showing_a_conflicting_rotation_warns_the_farmer_and_a_fitting_one_does_not():
    script, _ = cite_rotation("rot_conventional", "Звичайна сівозміна: ризик нестачі води {ref}. Вона містить соняшник, "
                                                  "який ви виключили.")
    shown = turn("Порівняй сівозміни.", script, NO_SUNFLOWER)
    assert any("did not apply your conditions" in (w.get("detail") or "") for w in shown["warnings"])
    script, _ = cite_rotation("rot_adaptive", "Адаптивна сівозміна: ризик нестачі води {ref}; це розрахунок.")
    fitting = turn("Порівняй сівозміни.", script, NO_SUNFLOWER)
    assert not any("did not apply your conditions" in (w.get("detail") or "") for w in fitting["warnings"])


def rotation(*steps: tuple[int, str]) -> SimpleNamespace:
    return SimpleNamespace(sequence=[SimpleNamespace(year_index=y, crop_id=c) for y, c in steps])


def rule(ctype: str, value, status: str = "confirmed") -> Constraint:
    return TypeAdapter(Constraint).validate_json(json.dumps(
        {"id": f"pc_{ctype}", "type": ctype, "value": value, "status": status, "source": "farmer_ui"}))


# Four steps in three years: winter pea and sorghum share year 2 (a catch crop), like the fixture's rot_adaptive.
THREE_YEARS = rotation((1, "winter_wheat"), (2, "winter_pea"), (2, "sorghum"), (3, "maize"))


@pytest.mark.parametrize(("confirmed", "conflict"), [(3, False), (4, True), (2, True)],
                         ids=["same_length", "rotation_shorter", "rotation_longer"])
def test_rotation_length_counts_distinct_years_and_reports_any_difference_neutrally(confirmed, conflict):
    found = constraint_conflicts(THREE_YEARS, [rule("rotation_length_years", confirmed)])
    expected = [{"constraint_id": "pc_rotation_length_years", "type": "rotation_length_years", "years": 3,
                 "confirmed_years": confirmed}]
    assert found == (expected if conflict else [])  # never "limit" or "maximum": the contract does not say which


def test_only_confirmed_rules_are_checked_and_only_against_what_the_rotation_shows():
    assert constraint_conflicts(THREE_YEARS, [rule("excluded_crops", ["maize"])]) == [
        {"constraint_id": "pc_excluded_crops", "type": "excluded_crops", "crops": ["maize"]}]
    # Rejected and proposed rules never reach this check (compare_rotations passes confirmed ones only): see
    # test_rejected_and_proposed_conditions_never_count_as_rules for the full turn.
    assert constraint_conflicts(THREE_YEARS, [rule("irrigation_available", False), rule("organic_only", True)]) == []


def test_reported_constraint_weights_are_never_taken_as_applied_conditions():
    # The fixture's comparison reports weights for irrigation and fertilizer whatever was sent (a static M0 copy):
    # fertilizer_budget is only proposed on this field, so it was never sent, yet a weight is reported for it.
    script, seen = cite_rotation("rot_adaptive", "Ризик нестачі води {ref}; це розрахунок.")
    turn("Порівняй сівозміни.", script, NO_SUNFLOWER)
    result = seen["result"]
    assert "constraint_weights_applied" not in json.dumps(result) and "weights" not in result
    assert "fertilizer_budget" not in str(result["constraints_sent"])
    assert "never say these results take them into account" in result["constraints_note"]


# ------------------------------------------------------------------ what the answer may not say


def test_the_answer_cannot_claim_the_farmers_conditions_were_applied():
    claim = "Розрахунок враховує ваші підтверджені умови: ризик нестачі води {ref}."
    script, _ = cite_rotation("rot_adaptive", claim)
    message = turn("Порівняй сівозміни з моїми умовами.", script, NO_SUNFLOWER)
    assert "враховує ваші" not in message["blocks"][0]["markdown"]
    assert "constraints_applied_claim" in str(message["warnings"])
    honest = "Ваших умов розрахунок поки не враховує. Ризик нестачі води {ref}."
    script, _ = cite_rotation("rot_adaptive", honest)
    kept = turn("Порівняй сівозміни з моїми умовами.", script, NO_SUNFLOWER)
    assert "не враховує" in kept["blocks"][0]["markdown"] and "constraints_applied_claim" not in str(kept["warnings"])


def test_the_english_claim_is_caught_and_the_negation_allowed():
    script, _ = cite_rotation("rot_adaptive", "These results take into account your confirmed conditions: {ref}.")
    message = turn("Compare rotations with my conditions.", script, NO_SUNFLOWER, lang="en")
    assert "take into account your" not in message["blocks"][0]["markdown"]
    script, _ = cite_rotation("rot_adaptive", "The calculation does not take into account your conditions yet: {ref}.")
    kept = turn("Compare rotations with my conditions.", script, NO_SUNFLOWER, lang="en")
    assert "does not take into account" in kept["blocks"][0]["markdown"]


def test_an_invented_ranking_or_yield_is_removed():
    script, _ = cite_rotation("rot_adaptive", "Адаптивна сівозміна оптимальна і дасть на 20% більший урожай; ризик {ref}.")
    message = turn("Яка сівозміна краща?", script)
    md = message["blocks"][0]["markdown"]
    assert "оптимальна" not in md and "20%" not in md


# ------------------------------------------------------------------ without a simulation


def test_without_a_simulation_nothing_is_computed_and_a_qualitative_answer_passes():
    qualitative = ("Порахувати сівозміни зараз нема чим: розрахунок не підключено. Загалом бобові в сівозміні "
                   "лишають азот наступній культурі, а соняшник сильно висушує ґрунт. Бракує результату розрахунку "
                   "для вашого поля; можу допомогти підтвердити ваші умови, щоб порівняння було точнішим.")
    message = turn("Що краще: сівозміна з горохом чи з соняшником?", [say(qualitative)], sim=False)
    assert message["blocks"][0]["markdown"] == qualitative and not message["warnings"]
    assert not any(f["id"].startswith("sim.") for f in message["facts"])
    invented = turn("Що краще?", [say("З горохом урожай буде 6 т/га, з соняшником 4 т/га."),
                                 say("З горохом урожай буде 6 т/га, з соняшником 4 т/га.")], sim=False)
    assert "т/га" not in invented["blocks"][0]["markdown"]


def test_without_a_simulation_the_tool_is_not_offered_and_the_context_says_so():
    agent = LLMAgent(ScriptedProvider([say("Ок.")]))
    assert "compare_rotations" not in agent.registry
    context = user_message(as_request(body("Порівняй сівозміни.")), "uk", 20_000)  # no services: none connected
    assert '"rotation_simulation"' in context and '"yield_prediction"' in context


# ------------------------------------------------------------------ the rules-based demo and the prompt


def test_the_scripted_demo_names_the_broken_rule_and_says_conditions_are_not_applied():
    message = turn("Порівняй сівозміни.", demo_script, NO_SUNFLOWER)
    md = message["blocks"][0]["markdown"] if message["blocks"][0]["type"] == "text" else ""
    assert "не враховує" in md and "Тут є культура, яку ви виключили: соняшник" in md
    english = turn("Compare rotations.", demo_script, NO_SUNFLOWER, lang="en")
    assert "It includes a crop you excluded: sunflower" in english["blocks"][0]["markdown"]
    four = {**AT_MOST_TWO_YEARS, "value": 4}
    uk = turn("Порівняй сівозміни.", demo_script, four)["blocks"][0]["markdown"]
    en = turn("Compare rotations.", demo_script, four, lang="en")["blocks"][0]["markdown"]
    assert "Її тривалість (3 р.) не збігається з вашою підтвердженою умовою (4 р.)." in uk
    assert "Its length (3 years) differs from your confirmed condition (4 years)." in en
    assert "довша" not in uk and "longer than" not in en


def test_the_prompt_separates_the_kinds_of_evidence_and_the_farmers_decisions():
    for text in ("# Strategies", "Keep four kinds of evidence apart", '"rejected" means they said no',
                 "The rotation calculation does not use the farmer's conditions yet",
                 "farmer_constraint_conflicts", "not as something the calculation aimed for", "offer one next step"):
        assert text in SYSTEM_PROMPT, text
