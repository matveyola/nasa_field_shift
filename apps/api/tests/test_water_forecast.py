from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from field_shift.api import create_app
from field_shift.db import build_session_factory
from field_shift.jobs import repository
from field_shift.jobs.models import Job
from field_shift.jobs.worker import process_job
from field_shift.models import metadata
from field_shift.modules.geo_ml import refresh
from field_shift.modules.simulation import forecast
from field_shift.modules.simulation.root_zone_inputs import RootZoneRun, RootZoneWeatherDay
from field_shift.modules.weather.historical import WeatherUnavailableError
from field_shift.seed import seed_demo

NOW = datetime(2026, 8, 8, tzinfo=UTC)


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    factory = build_session_factory("sqlite://")
    engine = factory.kw["bind"]
    metadata.create_all(engine)
    with factory() as session:
        seed_demo(session)
    monkeypatch.setenv("WATER_FORECAST_REFRESH", "1")
    monkeypatch.setenv("MOISTURE_FORECAST_REFRESH", "0")
    monkeypatch.setenv("MOISTURE_ENABLE_EXPERIMENTAL", "0")
    monkeypatch.setattr(forecast, "active_root_zone_correction", lambda: None)
    monkeypatch.setattr(refresh, "active_root_zone_correction", lambda: None)
    monkeypatch.setattr(repository, "_database_time", lambda _session, now: now or NOW)

    def fetch(latitude, longitude, day):
        return RootZoneRun(
            day,
            latitude,
            longitude,
            "50R1",
            (0.22, 0.24, 0.25),
            tuple(
                RootZoneWeatherDay(
                    day + timedelta(days=lead),
                    lead,
                    2.0 if lead == 3 else 0.0,
                    4.0,
                    19.0,
                    17.0,
                    (0.21, 0.23, 0.24),
                )
                for lead in range(1, 6)
            ),
        )

    monkeypatch.setattr(forecast, "fetch_ifs_root_zone", fetch)
    try:
        yield factory
    finally:
        engine.dispose()


def scheduled(database, at: datetime) -> list[str]:
    refresh.enqueue_refresh(database, now=at)
    with database() as session:
        return list(
            session.scalars(
                select(Job.id).where(
                    Job.field_id == "F-03",
                    Job.inputs["analysis"].as_string() == "water_forecast",
                    Job.created_at == at,
                )
            )
        )


def test_durable_water_forecast_uses_fixed_run_and_preserves_mass_balance(database):
    with TestClient(create_app(session_factory=database)) as client:
        endpoint = "/api/water/forecast?field_id=F-03"
        assert client.get(endpoint).json() is None
        (job_id,) = scheduled(database, NOW)
        assert scheduled(database, NOW + timedelta(hours=1)) == []
        process_job(database, job_id, lease_seconds=135, now=NOW)
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["kind"] == "simulation" and job["analysis"] == "water_forecast"
        result = client.get(endpoint).json()
        assert result == job["result"]
        assert result["initialized_at"] == "2026-08-07T00:00:00Z"
        assert result["method"] == "fao56" and result["model_version"] is None
        assert result["initial_reserve_mm"] == pytest.approx(95.8)
        assert result["root_depth_m"] == 1
        assert result["capacity_mm"] == 150
        assert [day["lead_days"] for day in result["days"]] == [1, 2, 3, 4, 5]
        assert date.fromisoformat(result["days"][-1]["date"]) == NOW.date() + timedelta(days=4)
        for day in result["days"]:
            assert day["root_zone_estimate_mm"] is None
            assert day["reserve_mm"] == pytest.approx(
                day["opening_mm"] + day["rain_mm"] - day["water_use_mm"] - day["drainage_mm"]
            )


def test_source_outage_keeps_last_result_and_records_a_retry(database, monkeypatch):
    (first,) = scheduled(database, NOW)
    process_job(database, first, lease_seconds=135, now=NOW)

    def unavailable(*_args):
        raise WeatherUnavailableError("Archive timeout")

    monkeypatch.setattr(forecast, "fetch_ifs_root_zone", unavailable)
    next_day = NOW + timedelta(days=1)
    (second,) = scheduled(database, next_day)
    process_job(database, second, lease_seconds=135, now=next_day)
    with TestClient(create_app(session_factory=database)) as client:
        job = client.get(f"/api/jobs/{second}").json()
        assert job["status"] == "retrying"
        assert job["error"] == forecast.ROOT_ZONE_SOURCE_UNAVAILABLE
        assert client.get("/api/water/forecast?field_id=F-03").json()["initialized_at"] == (
            "2026-08-07T00:00:00Z"
        )


def test_forecast_started_before_boundary_edit_is_preserved_only_in_history(database, monkeypatch):
    (first,) = scheduled(database, NOW)
    with TestClient(create_app(session_factory=database)) as client:
        endpoint = "/api/water/forecast?field_id=F-03"
        fetch = forecast.fetch_ifs_root_zone

        def edit_while_worker_is_running(*args):
            field = next(item for item in client.get("/api/fields").json() if item["id"] == "F-03")
            ring = [[lon, lat + 0.0001] for lon, lat in field["geometry"]["coordinates"][0]]
            response = client.patch(
                "/api/fields/F-03/boundary",
                json={
                    "boundary_revision": 0,
                    "geometry": {"type": "Polygon", "coordinates": [ring]},
                },
            )
            assert response.status_code == 200
            return fetch(*args)

        with monkeypatch.context() as provider:
            provider.setattr(forecast, "fetch_ifs_root_zone", edit_while_worker_is_running)
            process_job(database, first, lease_seconds=135, now=NOW)

        archived = client.get(f"/api/jobs/{first}").json()
        assert archived["status"] == "succeeded" and archived["boundary_revision"] == 0
        assert archived["result"] is not None
        assert client.get(endpoint).json() is None

        after_edit = NOW + timedelta(seconds=1)
        (second,) = scheduled(database, after_edit)
        process_job(database, second, lease_seconds=135, now=after_edit)
        current = client.get(f"/api/jobs/{second}").json()
        assert current["status"] == "succeeded" and current["boundary_revision"] == 1
        assert client.get(endpoint).json() == current["result"]


def test_ml_estimate_is_separate_and_rolls_back_to_physical_forecast(database, monkeypatch):
    model = SimpleNamespace(
        version="test-root-zone",
        source_cycle="50R1",
        supports=lambda *_args: True,
        predict=lambda *_args: 80.0,
    )
    monkeypatch.setattr(forecast, "active_root_zone_correction", lambda: model)
    (job_id,) = scheduled(database, NOW)
    process_job(database, job_id, lease_seconds=135, now=NOW)
    with TestClient(create_app(session_factory=database)) as client:
        endpoint = "/api/water/forecast?field_id=F-03"
        result = client.get(endpoint).json()
        assert result["method"] == "ml"
        assert result["days"][0]["root_zone_estimate_mm"] == 80
        physical = result["days"][0]["reserve_mm"]
        assert physical != 80

        monkeypatch.setattr(forecast, "active_root_zone_correction", lambda: None)
        rolled_back = client.get(endpoint).json()
        assert rolled_back["method"] == "fao56" and rolled_back["model_version"] is None
        assert rolled_back["days"][0]["root_zone_estimate_mm"] is None
        assert rolled_back["days"][0]["reserve_mm"] == physical


def test_forecast_submission_rejects_scenario_overrides(database):
    with TestClient(create_app(session_factory=database)) as client:
        good = {
            "kind": "simulation",
            "field_id": "F-03",
            "inputs": {
                "analysis": "water_forecast",
                "source": "forecast",
                "season": 2026,
                "scenario": "baseline",
            },
        }
        assert client.post("/api/jobs", json=good).status_code == 202
        for invalid in ({"scenario": "dry_year"}, {"source": "historical"}, {"irrigation_mm": 8}):
            request = good | {"inputs": good["inputs"] | invalid}
            assert client.post("/api/jobs", json=request).status_code == 422
