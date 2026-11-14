"""Grounding checker: which numbers and phrases count as unsupported claims."""

import json

import pytest

from agent_service.grounding import check, redact, render_fact
from contracts.models import Fact

from .conftest import context

FACTS = {f["id"]: Fact.model_validate_json(json.dumps(f, ensure_ascii=False)) for f in context("poltava")["facts"]}


def grounded(text, retrieved=None, farmer="", simulation_ran=False, any_live=False):
    return check(text, retrieved=FACTS if retrieved is None else retrieved, known_ids=set(FACTS), farmer_text=farmer,
                 simulation_ran=simulation_ran, any_live_fact=any_live, lang="uk")


@pytest.mark.parametrize("text", [
    "За даними SMAP L4 і FAO-56 у 2025 році.",              # product names and a year
    "Є 3 варіанти:\n1. Жито.\n2. Вика.",                    # small counts and list markers
    "Поле `demo-poltava-01`, ревізія у дужках.",           # inline code
    "Дані за 2025-07-01, сценарій drought_2024.",           # ISO date, identifier
    "Шар NASADEM_SC та продукт SPL4SMGP v008.",             # digits glued to letters
    "Перші 2 роки — бобові.",                               # small integer with a time word
    "Для варіанту «спека +2,5 °C» порівняння немає.",       # a scenario's exact display name
    "No comparison for the “heat +2.5 °C” case.",
    "Дані за 01.07–31.07.2025.",                             # periods written the way the app shows them
    "Data for 1 Jul – 31 Jul 2025.",
])
def test_allowed_numbers(text):
    assert grounded(text).violations == []


@pytest.mark.parametrize("text", [
    "Волога 0,21 м³/м³.", "Волога 0.21.", "Врожай 45 ц/га.", "Опадів було 38 мм.", "Посухи почастішали на 32%.",
    "Роздільна здатність 30 м.", "pH 6.", "pH 7 — нейтральний.", "За 15 років стало тепліше.", "Температура +2 °C.",
    "Покрив 290 днів.", "Moisture is 0.21 m3/m3.",
    "Спека +3,5 °C.",  # only the exact scenario names are exempt
])
def test_flagged_numbers(text):
    assert {v.category for v in grounded(text).violations} == {"ungrounded_number"}


def test_farmers_own_integer_may_be_echoed():
    assert grounded("Для ваших 50 га.", farmer="У мене 50 га").violations == []
    assert grounded("Для ваших 50 га.", farmer="У мене поле").violations  # not said by the farmer


@pytest.mark.parametrize("text, category", [
    ("Симуляція показала, що соя краща.", "simulation_claim"),
    ("The simulation shows maize fails.", "simulation_claim"),
    ("Я провів симуляцію посухи.", "simulation_claim"),
    ("Це дані у реальному часі.", "live_claim"),
    ("These are live satellite readings.", "live_claim"),
    ("За фото ваш pH кислий.", "photo_lab_claim"),
    ("From the photo, nitrogen is low.", "photo_lab_claim"),
])
def test_claim_patterns(text, category):
    assert category in {v.category for v in grounded(text).violations}


def test_claims_allowed_when_backed():
    assert not grounded("The simulation shows it.", simulation_ran=True).violations
    assert not grounded("Дані у реальному часі.", any_live=True).violations


def test_fact_tokens_render_value_and_origin():
    g = grounded("pH {{fact:f_ph_0_30}}, ML {{ fact : f_sm_30m_mean_2025_07 }}.")
    assert g.violations == [] and g.fact_ids == ["f_ph_0_30", "f_sm_30m_mean_2025_07"]
    assert "**6,9 (6,4–7,3)**" in g.markdown and "(неперевірена оцінка)" in g.markdown
    assert "{{" not in g.markdown


def test_unknown_and_unretrieved_tokens_are_violations():
    g = grounded("{{fact:f_nope}} {{fact:f_ph_0_30}}", retrieved={})
    assert [v.category for v in g.violations] == ["unknown_fact", "fact_not_retrieved"]
    assert "{{" not in g.markdown  # nothing unverified is rendered


def test_redact_removes_only_offending_sentences():
    text = "Перше речення без чисел. Врожай буде 45 ц/га. Третє речення."
    g = grounded(text)
    assert redact(text, g.violations) == "Перше речення без чисел. Третє речення."


def test_unavailable_fact_renders_without_value():
    ph = context("mykolaiv")["facts"][0]
    assert render_fact(Fact.model_validate_json(json.dumps(ph, ensure_ascii=False)), "en") == "no data (SoilGrids world soil map)"


@pytest.mark.parametrize(("scenario", "lang", "shown"), [
    ("typical", "uk", "розрахунок сівозміни, варіант «типові умови»"),
    ("typical", "en", "rotation calculation, case “typical conditions”"),
    ("drought_2024", "uk", "варіант «посуха (як у 2024)»"),
    ("frost_2027", "en", "case “frost_2027”"),  # unknown id: shown as is, never guessed
])
def test_simulation_values_name_the_scenario_for_people(scenario, lang, shown):
    # Task 13: the explanation shows a display name; the id itself stays in provenance.method.
    base = Fact.model_validate_json(json.dumps(context("poltava")["facts"][0], ensure_ascii=False))
    prov = base.provenance.model_copy(update={"method": f"fixture-replay; scenario={scenario}"})
    fact = base.model_copy(update={"id": "sim.rot_a.water_risk", "domain": "rotation", "provenance": prov})
    assert shown in render_fact(fact, lang)
    assert fact.provenance.method.endswith(f"scenario={scenario}")


def test_demo_values_are_marked_one_by_one_only_next_to_real_ones():
    # Owner's call (2026-11-12): plain data gets no note; when every value is demo data the answer's notice says so
    # once, but a demo value next to a real one is marked, so the text never passes demo data off as real.
    sm, ph = FACTS["f_sm_rootzone_2025_07"], FACTS["f_ph_0_30"]
    assert sm.provenance.data_status == ph.provenance.data_status == "fixture"
    text = "Волога {{fact:f_sm_rootzone_2025_07}}, pH {{fact:f_ph_0_30}}."
    all_demo = grounded(text, retrieved={sm.id: sm, ph.id: ph})
    assert all_demo.violations == [] and "демо" not in all_demo.markdown
    real_ph = ph.model_copy(update={"provenance": ph.provenance.model_copy(update={"data_status": "live"})})
    mixed = grounded(text, retrieved={sm.id: sm, ph.id: real_ph})
    assert "**0,21 м³/м³** (демо)" in mixed.markdown and "**6,9 (6,4–7,3)**." in mixed.markdown


def test_demo_data_is_never_called_actual_or_real_when_everything_is_demo():
    # Live slip (2026-11-12): a demo field's data offered as "фактичні історичні спостереження із супутників".
    def run(text, demo_only):
        return check(text, retrieved={}, known_ids=set(FACTS), farmer_text="", simulation_ran=False, any_live_fact=False,
                     lang="uk", demo_only=demo_only)
    slip = "Можемо переглянути фактичні історичні спостереження із супутників."
    assert {v.category for v in run(slip, True).violations} == {"demo_as_real"}
    assert run(slip, False).violations == []  # real data may be called real
    for honest in ("Це демо-дані, не справжній вимір.", "У реальних умовах поля все може бути інакше."):
        assert run(honest, True).violations == []
