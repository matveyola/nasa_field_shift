"""Emitted M0 evidence keeps the modeled-water domain caveat under the wire bound."""

from datetime import UTC, datetime, timedelta

import pytest
from agent_contract_schema import validate_agent_wire
from field_shift_science.fields.schemas import FieldRead
from test_water_applicability import GEOMETRY, evaluate, source

from field_shift.modules.agent.field_context import build_field_context

NOW = datetime(2026, 11, 15, 12, tzinfo=UTC)
FIELD = FieldRead(
    id="synthetic",
    name="Synthetic thermal test",
    crop="Barley",
    area_ha=1,
    geometry=GEOMETRY,
    boundary_revision=0,
)


def current(monkeypatch, temperature):
    return evaluate(monkeypatch, source(temperature)).model_copy(update={"generated_at": NOW})


@pytest.mark.parametrize("temperature", [-42.1, 0, 17, None])
@pytest.mark.parametrize("long_provider", [False, True])
def test_emitted_schema_valid_forecast_facts_keep_phase_caveat(
    monkeypatch, temperature, long_provider
):
    forecast = current(monkeypatch, temperature if temperature is not None else 17)
    if temperature is None:
        forecast = forecast.model_copy(update={"applicability": None})
    if long_provider:
        forecast = forecast.model_copy(update={"provider": "Long provider description " * 50})
    context = build_field_context(FIELD, season=2026, locale="en", now=NOW, forecast=forecast)
    validate_agent_wire("FieldContext", context)
    assert len(context["facts"]) == 4
    facts = {fact["id"]: fact for fact in context["facts"]}
    assert facts["f_forecast_min_root_zone_reserve"]["quantity"] == {
        "value": round(min(d.reserve_mm for d in forecast.days), 1),
        "unit": "mm",
    }
    assert facts["f_water_stress_threshold"]["quantity"] == {
        "value": forecast.threshold_mm,
        "unit": "mm",
    }
    assert not any("temperature" in fact["id"] for fact in context["facts"])
    for item in context["facts"] + context["timeseries_index"]:
        method = item["provenance"]["method"]
        assert len(method) <= 300
        assert "Freezing/thawing/dormancy not modeled" in method
        assert "root-zone ice, liquid water and crop suitability unassessed" in method
        if temperature is None:
            assert method.startswith("Applicability unknown")
            assert "<=0" not in method and ">0" not in method
        else:
            assert "shallow 0–7 cm daily-mean soil forecast" in method
            assert method.startswith("review_required" if temperature <= 0 else "not_assessed")
        assert item["provenance"]["source"]["url"] == forecast.source_url


@pytest.mark.parametrize("change", ["wrong_field", "wrong_year", "expired", "future"])
def test_applicability_metadata_never_bypasses_current_context_fences(monkeypatch, change):
    forecast = current(monkeypatch, -42.1)
    updates = {
        "wrong_field": {"field_id": "other"},
        "wrong_year": {"season": 2025},
        "expired": {"initialized_at": NOW - timedelta(days=5)},
        "future": {"generated_at": NOW + timedelta(days=1)},
    }[change]
    context = build_field_context(
        FIELD, season=2026, locale="en", now=NOW, forecast=forecast.model_copy(update=updates)
    )
    validate_agent_wire("FieldContext", context)
    assert context["facts"] == context["timeseries_index"] == context["available_charts"] == []
