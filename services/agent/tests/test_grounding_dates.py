"""Captured final QA evidence; draft wording below is reconstructed, not a provider log."""

import json
from pathlib import Path

import pytest
from agent_service.grounding import check, redact
from contracts.models import Fact

CAPTURE = json.loads(
    (Path(__file__).parent / "fixtures/grounding-weather-t01.json").read_text()
)
FACTS = {f["id"]: Fact.model_validate_json(json.dumps(f)) for f in CAPTURE["facts"]}


def ground(text, *, facts=None, farmer="", lang="en", unverified=frozenset()):
    retrieved = FACTS if facts is None else facts
    return check(
        text,
        retrieved=retrieved,
        known_ids=set(FACTS),
        farmer_text=farmer,
        simulation_ran=False,
        any_live_fact=True,
        lang=lang,
        unverified=unverified,
    )


@pytest.mark.parametrize(
    "date",
    [
        "November 14, 2026",
        "November 14 2026",
        "14 November 2026",
        "14 Nov 2026",
        "14 листопада 2026 року",
        "14 листопада 2026",
        "2026-11-14",
        "14.11.2026",
    ],
)
def test_current_retrieved_fact_supports_its_exact_date(date):
    text = f"{date}: {{{{fact:wx.2026-11-14.alert}}}}."
    result = ground(text)
    assert not result.violations
    assert date in result.markdown and "frost possible" in result.markdown


@pytest.mark.parametrize(
    "period",
    [
        "November 1, 2025 – November 14, 2026",
        "1 November 2025 to 14 November 2026",
        "1 листопада 2025 року – 14 листопада 2026 року",
        "2025-11-01–2026-11-14",
        "01.11.2025–14.11.2026",
    ],
)
def test_full_cross_year_period_preserves_metric_context(period):
    text = f"Rain for {period}: {{{{fact:season.rain}}}}."
    result = ground(text)
    assert not result.violations
    assert period in result.markdown and "1415.1 mm" in result.markdown


@pytest.mark.parametrize(
    "date",
    [
        "November 13, 2026",
        "14 November 2027",
        "15 листопада 2026 року",
        "2027-11-14",
        "2026-11-15",  # Another retrieved day's date cannot label this fact.
        "2026-02-31",
        "13.11.2026",
        "14/11/2026",
        "11/14/2026",
        "November 14",  # A matching month/day without its year is not a full date.
    ],
)
def test_wrong_invalid_ambiguous_or_other_facts_date_is_rejected(date):
    text = f"{date}: {{{{fact:wx.2026-11-14.alert}}}}."
    result = ground(text, farmer=f"My old history said {date}")
    assert {v.category for v in result.violations} == {"ungrounded_date"}
    assert not redact(text, result.violations)


def test_range_cannot_borrow_start_day_from_a_different_year():
    result = ground("Rain for 1 Nov – 14 Nov 2026: {{fact:season.rain}}.")
    assert {v.category for v in result.violations} == {"ungrounded_date"}


def test_both_years_cannot_be_omitted_from_a_cross_year_period():
    assert ground("Rain for 1 Nov – 14 Nov: {{fact:season.rain}}.").violations


@pytest.mark.parametrize("separator", [", ", "; ", " and "])
def test_multiple_fact_dates_cannot_be_swapped_in_one_sentence(separator):
    bad = (
        "November 16, 2026: {{fact:wx.2026-11-14.alert}}"
        + separator
        + "November 14, 2026: {{fact:wx.2026-11-16.alert}}."
    )
    assert {v.category for v in ground(bad).violations} == {"ungrounded_date"}
    good = (
        "November 14, 2026: {{fact:wx.2026-11-14.alert}}"
        + separator
        + "November 16, 2026: {{fact:wx.2026-11-16.alert}}."
    )
    assert not ground(good).violations


def test_date_after_its_reference_and_range_after_another_reference():
    text = (
        "{{fact:wx.2026-11-14.alert}} on November 14, 2026; "
        "{{fact:season.rain}} for November 1, 2025 – November 14, 2026."
    )
    assert not ground(text).violations


def test_ungrounded_history_dates_and_known_but_unretrieved_facts_are_not_evidence():
    text = "November 14, 2026: {{fact:wx.2026-11-14.alert}}."
    result = ground(
        text, facts={}, farmer="Field called November 14, 2026; history 2026-11-14"
    )
    assert {v.category for v in result.violations} == {
        "fact_not_retrieved",
        "ungrounded_date",
    }
    result = ground(text, unverified={"wx.2026-11-14.alert"})
    assert "ungrounded_date" in {v.category for v in result.violations}


@pytest.mark.parametrize("status", ["unavailable"])
def test_unavailable_temporal_evidence_does_not_grant_dates(status):
    original = FACTS["wx.2026-11-14.alert"]
    fact = original.model_copy(
        update={
            "provenance": original.provenance.model_copy(update={"data_status": status})
        }
    )
    assert ground(
        "2026-11-14: {{fact:wx.2026-11-14.alert}}.", facts={fact.id: fact}
    ).violations


@pytest.mark.parametrize(
    "claim",
    ["14 mm", "2026 UAH", "1415.1 mm", "15 t/ha", "2026-11-14 USD"],
)
def test_date_authority_never_allows_measurement_or_money_digits(claim):
    result = ground(
        f"November 14, 2026: {{{{fact:wx.2026-11-14.alert}}}}; cost {claim}."
    )
    assert result.violations


def test_source_retrieval_date_is_not_a_measurement_period():
    # Rain was retrieved on Nov15, but its aggregation ends on Nov14.
    assert ground("Rain through November 15, 2026: {{fact:season.rain}}.").violations


@pytest.mark.parametrize("claim", ["14 mm", "11 t/ha", "2026 UAH"])
def test_farmer_date_digits_are_not_scalar_inputs(claim):
    assert ground(claim, farmer="I planted on November 14, 2026.").violations


def test_unverified_fact_date_cannot_reappear_in_server_rendering():
    result = ground("{{fact:season.rain}}", unverified={"season.rain"})
    assert "2025" not in result.markdown and "2026" not in result.markdown
    assert "not confirmed by the data service" in result.markdown
    assert "total rainfall" in result.markdown


@pytest.mark.parametrize("lang", ["en", "uk"])
def test_captured_bare_fact_regression_gets_metric_window_and_source(lang):
    # These are the fact tokens represented by the six bare values in T01's
    # captured final answer, not a claim to know the model's original draft.
    raw = " ".join(f"{{{{fact:{fid}}}}}" for fid in FACTS)
    result = ground(raw, lang=lang)
    assert not result.violations
    assert "Open-Meteo" in result.markdown
    assert all(str(year) in result.markdown for year in (2025, 2026))
    if lang == "en":
        assert "total rainfall" in result.markdown
        assert "water lost to evaporation" in result.markdown
        assert "rain minus evaporation" in result.markdown
        assert all(f"{day} Nov 2026" in result.markdown for day in (14, 15, 16))
        assert "1 Nov 2025 – 14 Nov 2026" in result.markdown
    else:
        assert "сума опадів" in result.markdown
        assert "опади мінус випаровування" in result.markdown
        assert all(f"{day}.11.2026" in result.markdown for day in (14, 15, 16))
        assert "01.11.2025–14.11.2026" in result.markdown


def test_redaction_does_not_leave_unidentified_surviving_values():
    raw = "Invented yield 45 t/ha.\n{{fact:season.rain}} {{fact:season.et0}}"
    checked = ground(raw)
    assert {v.category for v in checked.violations} == {"ungrounded_number"}
    final = ground(redact(raw, checked.violations))
    assert not final.violations
    assert "45" not in final.markdown
    assert "total rainfall" in final.markdown and "1 Nov 2025" in final.markdown


def test_missing_period_is_explicit_and_never_replaced_by_retrieval_date():
    original = FACTS["season.rain"]
    fact = original.model_copy(
        update={"provenance": original.provenance.model_copy(update={"temporal": None})}
    )
    result = ground("{{fact:season.rain}}", facts={fact.id: fact})
    assert (
        "total rainfall" in result.markdown and "period not supplied" in result.markdown
    )
    assert "2026" not in result.markdown


def test_unknown_metric_key_is_not_guessed_from_the_id():
    original = FACTS["season.rain"]
    fact = original.model_copy(update={"label_key": "fact.unknown_123"})
    result = ground("{{fact:season.rain}}", facts={fact.id: fact})
    assert "unknown" not in result.markdown and "123" not in result.markdown
    assert "1 Nov 2025" in result.markdown


@pytest.mark.parametrize(
    "period", ["14 Nov – 16 Nov 2026", "16 Nov 2026 – 14 Nov 2026"]
)
def test_separate_day_facts_cannot_authorize_a_complete_or_reversed_period(period):
    text = (
        f"{period}: {{{{fact:wx.2026-11-14.alert}}}} {{{{fact:wx.2026-11-16.alert}}}}."
    )
    assert ground(text).violations


def test_actual_llm_loop_preserves_verified_dates_and_redacts_unverified_yield():
    from agent_service.llm.scripted import ScriptedProvider, call, say
    from agent_service.llm_agent import LLMAgent
    from agent_service.tools import FACTS_SPEC, FactsArgs, Tool

    from .conftest import request_body
    from .test_trust_boundary import final

    # Offline scripted output exercises the real repair/redaction/SSE path. It
    # reconstructs wording around captured facts and makes no paid call.
    body = request_body("poltava", "Explain the weather dates.", locale="en")
    body["field_context"]["facts"] = []
    body["history"] = [
        {"role": "assistant", "text": "Yield 45 t/ha on November 14, 2027."}
    ]
    draft = (
        "November 14, 2026: {{fact:wx.2026-11-14.alert}}. "
        "Rain for November 1, 2025 – November 14, 2026: {{fact:season.rain}}. "
        "Yield is 45 t/ha.\n{{fact:season.et0}}"
    )
    provider = ScriptedProvider(
        [
            call(("get_field_facts", {"domains": [], "fact_ids": []})),
            say(draft),
            say(draft),
        ]
    )

    async def replay_captured_server_evidence(args, state):
        # Installed-tool test double, not unsigned browser assertions. Real
        # external tools assign this same authority after their provider call.
        state.external_facts.update(FACTS)
        state.retrieved.update(FACTS)
        return {"facts": CAPTURE["facts"]}, list(FACTS)

    registry = {
        "get_field_facts": Tool(FACTS_SPEC, FactsArgs, replay_captured_server_evidence)
    }
    message = final(LLMAgent(provider, registry=registry), body)["message"]
    text = message["blocks"][0]["markdown"]
    assert "November 14, 2026" in text and "November 1, 2025" in text
    assert "water lost to evaporation" in text and "Open-Meteo" in text
    assert "45" not in text and "2027" not in text
    assert message["warnings"]
    assert all(f["id"] in FACTS for f in message["facts"])
