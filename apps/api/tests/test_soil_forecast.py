from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from field_shift.api import create_app
from field_shift.db import build_session_factory
from field_shift.jobs import repository
from field_shift.jobs.models import Job
from field_shift.jobs.worker import process_job
from field_shift.models import metadata
from field_shift.modules.geo_ml import forecast, refresh
from field_shift.modules.geo_ml.era5_land import MoistureSourceError
from field_shift.modules.geo_ml.forecast_model import (
    ForecastCorrection,
    forecast_version,
    get_forecast_correction,
)
from field_shift.modules.geo_ml.ifs import CURRENT_MODEL_CYCLE, HORIZON, fetch_ifs_run
from field_shift.seed import seed_demo

NOW = datetime(2026, 11, 9, 6, tzinfo=UTC)
ISSUE = date(2026, 11, 8)


def payload(day: date) -> dict:
    moist, deep = "soil_moisture_0_to_7cm_mean", "soil_moisture_7_to_28cm_mean"
    soil_temp, air_temp = "soil_temperature_0_to_7cm_mean", "temperature_2m_mean"
    rain, et0 = "precipitation_sum", "et0_fao_evapotranspiration"
    return {
        "latitude": 41.7,
        "longitude": -93.4,
        "utc_offset_seconds": 0,
        "daily_units": {
            moist: "m³/m³",
            deep: "m³/m³",
            soil_temp: "°C",
            air_temp: "°C",
            rain: "mm",
            et0: "mm",
        },
        "daily": {
            "time": [(day + timedelta(days=lead)).isoformat() for lead in range(HORIZON + 1)],
            moist: [0.35, 0.34, 0.32, 0.3, 0.29, 0.28],
            deep: [0.4] * (HORIZON + 1),
            soil_temp: [7.5] * (HORIZON + 1),
            air_temp: [6.8] * (HORIZON + 1),
            rain: [None, 0, 0, 1, 0, 0],
            et0: [None, 1, 1.1, 1.2, 1.3, 1.4],
        },
    }


def test_pinned_run_supplies_only_complete_next_days_and_correct_units(monkeypatch):
    monkeypatch.delenv("OPEN_METEO_API_KEY", raising=False)

    def transport(url: str):
        address = urlsplit(url)
        query = parse_qs(address.query)
        assert address.hostname == "single-runs-api.open-meteo.com"
        assert query["run"] == ["2026-11-08T00:00"]
        assert query["models"] == ["ecmwf_ifs"]
        return payload(ISSUE)

    run = fetch_ifs_run(41.705, -93.413, ISSUE, fetch=transport)
    assert run.initialized_on == ISSUE
    assert run.initial_moisture == 0.35
    assert run.model_cycle == "50R1"
    assert [day.lead_days for day in run.days] == [1, 2, 3, 4, 5]
    assert run.days[0].date == NOW.date()
    assert run.days[-1].date == NOW.date() + timedelta(days=4)


@pytest.mark.parametrize("broken", ["timestamp", "unit", "missing", "wrong location"])
def test_ifs_invalid_runs_are_retryable_source_failures(broken):
    data = payload(ISSUE)
    if broken == "timestamp":
        data["daily"]["time"][-1] = "2026-11-14"
    elif broken == "unit":
        data["daily_units"]["precipitation_sum"] = "in"
    elif broken == "missing":
        data["daily"]["soil_moisture_0_to_7cm_mean"][4] = None
    else:
        data["longitude"] = 50
    with pytest.raises(MoistureSourceError):
        fetch_ifs_run(41.705, -93.413, ISSUE, fetch=lambda _: data)


def test_durable_forecast_deduplicates_and_keeps_last_run_on_outage(monkeypatch):
    monkeypatch.setenv("WATER_FORECAST_REFRESH", "0")
    factory = build_session_factory("sqlite://")
    engine = factory.kw["bind"]
    metadata.create_all(engine)
    with factory() as session:
        seed_demo(session)
    monkeypatch.setenv("MOISTURE_FORECAST_REFRESH", "1")
    monkeypatch.setenv("MOISTURE_ENABLE_EXPERIMENTAL", "0")
    monkeypatch.setattr(repository, "_database_time", lambda _session, now: now or NOW)
    monkeypatch.setattr(forecast, "get_forecast_correction", lambda: None)

    def fetch(latitude, longitude, day):
        return fetch_ifs_run(latitude, longitude, day, fetch=lambda _: payload(day))

    monkeypatch.setattr(forecast, "fetch_ifs_run", fetch)

    def jobs(at):
        refresh.enqueue_refresh(factory, now=at)
        with factory() as session:
            return list(
                session.scalars(
                    select(Job.id).where(
                        Job.inputs["source"].as_string() == "forecast",
                        Job.field_id == "F-03",
                        Job.created_at == at,
                    )
                )
            )

    try:
        with TestClient(create_app(session_factory=factory)) as client:
            endpoint = "/api/fields/F-03/moisture-forecast"
            assert client.get(endpoint).json() is None
            scheduled = jobs(NOW)
            assert len(scheduled) == 1
            assert jobs(NOW + timedelta(hours=1)) == []
            process_job(factory, scheduled[0], lease_seconds=135, now=NOW)
            result = client.get(endpoint).json()
            assert result["initialized_at"] == "2026-11-08T00:00:00Z"
            assert result["model_version"] is None
            assert result["days"][0]["value"] == result["days"][0]["ifs_value"] == 0.34

            def outage(*_args):
                raise MoistureSourceError("ECMWF temporarily unavailable.")

            monkeypatch.setattr(forecast, "fetch_ifs_run", outage)
            tomorrow = NOW + timedelta(days=1)
            later = jobs(tomorrow)
            assert len(later) == 1
            process_job(factory, later[0], lease_seconds=135, now=tomorrow)
            assert client.get(f"/api/jobs/{later[0]}").json()["status"] == "retrying"
            assert client.get(endpoint).json() == result
            assert client.get("/api/fields/F-03/moisture?source=forecast").status_code == 422
            invalid = client.post(
                "/api/jobs",
                json={
                    "kind": "simulation",
                    "field_id": "F-03",
                    "inputs": {"season": 2026, "source": "forecast"},
                },
            )
            assert invalid.status_code == 422
    finally:
        engine.dispose()


def test_reading_saved_forecast_after_model_rollback_uses_raw_ifs(monkeypatch):
    factory = build_session_factory("sqlite://")
    engine = factory.kw["bind"]
    metadata.create_all(engine)
    with factory() as session:
        seed_demo(session)
    correction = SimpleNamespace(
        version="test-forecast",
        source_cycle="50R1",
        supports=lambda *_args: True,
        predict=lambda *_args: 0.25,
        interval_half_width=[0.1] * HORIZON,
        validation={"leads": {str(lead): {"interval_coverage": 0.8} for lead in range(1, 6)}},
    )
    monkeypatch.setattr(forecast, "active_forecast_correction", lambda: correction)
    monkeypatch.setattr(
        forecast,
        "fetch_ifs_run",
        lambda lat, lon, day: fetch_ifs_run(lat, lon, day, fetch=lambda _: payload(day)),
    )
    try:
        with factory.begin() as session:
            job = repository.submit(
                session,
                kind="geo_ml",
                field_id="F-03",
                inputs={"season": 2026, "source": "forecast"},
                now=NOW,
            )
        process_job(factory, job.id, lease_seconds=135, now=NOW)
        with factory() as session:
            corrected = forecast.latest_moisture_forecast(session, "F-03")
            assert corrected.model_version == "test-forecast"
            assert corrected.days[0].value == 0.25
            assert corrected.days[0].interval is not None

            monkeypatch.setattr(forecast, "active_forecast_correction", lambda: None)
            rolled_back = forecast.latest_moisture_forecast(session, "F-03")
            assert rolled_back.model_version is None
            assert rolled_back.days[0].value == 0.34
            assert rolled_back.days[0].interval is None
    finally:
        engine.dispose()


def test_bundled_correction_rejects_promotion_without_passing_evidence():
    model = get_forecast_correction()
    assert model is not None
    assert model.validation["independent_farms"]["sites"] >= 3
    assert model.promoted == all(model.validation["gate"].values())
    assert model.validation["gate"]["serving_cycle_validated"] == (
        model.source_cycle == CURRENT_MODEL_CYCLE
    )

    candidate = model.model_dump(mode="json", exclude={"version"})
    candidate["promoted"] = True
    candidate["validation"]["gate"]["improvement"] = False
    with pytest.raises(ValidationError, match="Invalid soil forecast model artifact"):
        ForecastCorrection.model_validate({"version": forecast_version(candidate), **candidate})
