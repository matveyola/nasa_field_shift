"""Synthetic temperature contrasts test interpretation, never geographic accuracy."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from field_shift_science.fields.schemas import Polygon
from field_shift_science.simulation.root_zone_inputs import RootZoneRun, RootZoneWeatherDay
from field_shift_science.simulation.schemas import WaterForecast

from field_shift.modules.simulation import forecast

GEOMETRY = Polygon(type="Polygon", coordinates=[[(30, 50), (30.01, 50), (30.01, 50.01), (30, 50)]])
INITIALIZED = date(2026, 11, 14)


def source(temperature):
    return RootZoneRun(
        INITIALIZED,
        50,
        30,
        "50R1",
        (0.22, 0.24, 0.25),
        tuple(
            RootZoneWeatherDay(
                INITIALIZED + timedelta(days=i), i, 2, 4, 10, temperature, (0.21, 0.23, 0.24)
            )
            for i in range(1, 6)
        ),
    )


def evaluate(monkeypatch, run):
    monkeypatch.setattr(forecast, "fetch_ifs_root_zone", lambda *_: run)
    monkeypatch.setattr(forecast, "active_root_zone_correction", lambda: None)
    return forecast.run_water_forecast(
        "synthetic", "Barley", GEOMETRY, datetime(2026, 11, 15, tzinfo=UTC)
    )


@pytest.mark.parametrize(
    "temperature,status",
    [
        (-42.1, "review_required"),
        (0, "review_required"),
        (0.01, "not_assessed"),
        (17, "not_assessed"),
    ],
)
def test_source_temperature_caveat_never_changes_reference_balance(
    monkeypatch, temperature, status
):
    warm = evaluate(monkeypatch, source(17))
    result = evaluate(monkeypatch, source(temperature))
    assert result.days == warm.days
    assert result.initial_reserve_mm == warm.initial_reserve_mm
    assert result.capacity_mm == warm.capacity_mm and result.threshold_mm == warm.threshold_mm
    assert result.physical_model_version == warm.physical_model_version
    assert result.method == "fao56" and result.model_version is None
    diagnostic = result.applicability
    assert diagnostic is not None and diagnostic.status == status
    assert diagnostic.source_kind == "forecast"
    assert diagnostic.source_variable == "soil_temperature_0_to_7cm_mean"
    assert diagnostic.depth_cm == (0, 7) and diagnostic.unit == "°C"
    assert diagnostic.aggregation == "daily_mean"
    assert diagnostic.initialized_at == datetime(2026, 11, 14, tzinfo=UTC)
    assert [d.date for d in diagnostic.soil_temperature] == [d.date for d in result.days]
    assert [d.mean_c for d in diagnostic.soil_temperature] == [temperature] * 5
    assert any("dormancy" in item and "liquid" in item for item in result.limitations)


def test_one_cold_day_is_retained_not_replaced_by_average_or_warm_air(monkeypatch):
    run = source(15)
    run = replace(
        run, days=(run.days[0], replace(run.days[1], soil_temperature_c=-1), *run.days[2:])
    )
    result = evaluate(monkeypatch, run)
    assert result.applicability.status == "review_required"
    assert [d.mean_c for d in result.applicability.soil_temperature] == [15, -1, 15, 15, 15]


@pytest.mark.parametrize("missing", [True, False])
def test_archived_payload_without_diagnostic_remains_unknown_and_keeps_numbers(
    monkeypatch, missing
):
    current = evaluate(monkeypatch, source(-10))
    payload = current.model_dump(mode="json")
    if missing:
        payload.pop("applicability")
    else:
        payload["applicability"] = None
    archived = WaterForecast.model_validate(payload)
    assert archived.applicability is None
    assert archived.days == current.days
    assert archived.initial_reserve_mm == current.initial_reserve_mm
