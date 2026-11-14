from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from field_shift.api import create_app
from field_shift.db import build_session_factory
from field_shift.jobs import repository
from field_shift.jobs.dispatcher import run_once
from field_shift.jobs.models import Job
from field_shift.jobs.settings import JobRuntimeSettings
from field_shift.jobs.worker import process_job
from field_shift.models import metadata
from field_shift.modules.geo_ml import ml_layer, refresh, service
from field_shift.modules.geo_ml.era5_land import (
    UNIT,
    VARIABLE,
    ERA5LandMoistureModel,
    MoistureSourceError,
)
from field_shift.modules.geo_ml.moisture_features import MoistureFeatures, MoistureFeatureSeries
from field_shift.modules.geo_ml.moisture_model import MoisturePrediction
from field_shift.seed import seed_demo

TODAY = datetime(2026, 8, 8, 1, tzinfo=UTC)
LATEST = date(2026, 8, 2)
LATEST_HISTORICAL = LATEST + timedelta(days=1)


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    monkeypatch.setenv("WATER_FORECAST_REFRESH", "0")
    monkeypatch.setenv("MOISTURE_FORECAST_REFRESH", "0")
    factory = build_session_factory("sqlite://")
    engine = factory.kw["bind"]
    metadata.create_all(engine)
    with factory() as session:
        seed_demo(session)

    def supports_location(latitude, longitude):
        return 40 <= latitude <= 43 and -94 <= longitude <= -93

    def fetch_features(latitude, longitude, start, end, *, api_key):
        assert supports_location(latitude, longitude)
        return feature_series(start, end)

    model = SimpleNamespace(
        version="test-model",
        promoted=True,
        interval_level=0.9,
        validation={"independent_farms": {"interval_coverage": 0.82}},
        supports_location=supports_location,
        supports_date=lambda _day: True,
        predict=lambda day: MoisturePrediction(day.era5_moisture + 0.04, 0.23, 0.43),
    )
    monkeypatch.setattr(refresh, "get_moisture_model", lambda: model)
    monkeypatch.setattr(ml_layer, "get_moisture_model", lambda: model)
    monkeypatch.setattr(ml_layer, "fetch_moisture_features", fetch_features)
    monkeypatch.setattr(service, "HISTORICAL_GEO_MODEL", ERA5LandMoistureModel(raw_archive))
    monkeypatch.setattr(repository, "_database_time", lambda _session, now: now or TODAY)
    try:
        yield factory
    finally:
        engine.dispose()


def run_queue(factory: sessionmaker[Session], at: datetime) -> None:
    run_once(
        factory,
        lambda delivery: process_job(factory, delivery.job_id, lease_seconds=135, now=at),
        settings=JobRuntimeSettings(),
        now=at,
    )


def feature_series(start, end, *, frozen=None):
    days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    features = tuple(MoistureFeatures(day, (0.29,), day == frozen) for day in days)
    return MoistureFeatureSeries(features, 41.7, -93.4)


def raw_archive(url):
    args = parse_qs(urlsplit(url).query)
    first = date.fromisoformat(args["start_date"][0])
    last = date.fromisoformat(args["end_date"][0])
    times = [
        f"{first + timedelta(days=offset)}T{hour:02}:00"
        for offset in range((last - first).days + 1)
        for hour in range(24)
    ]
    return {
        "latitude": round(float(args["latitude"][0]), 1),
        "longitude": round(float(args["longitude"][0]), 1),
        "utc_offset_seconds": 0,
        "hourly_units": {VARIABLE: UNIT},
        "hourly": {
            "time": times,
            VARIABLE: [0.29] * len(times),
        },
    }


def test_boundary_edit_fences_queued_jobs_and_requeues_current_geometry(database):
    with TestClient(create_app(session_factory=database)) as client:
        endpoint = "/api/fields/F-03/moisture?source=historical"
        assert refresh.enqueue_refresh(database, now=TODAY) == 6
        run_queue(database, TODAY)
        assert client.get(endpoint).json()["date"] == LATEST_HISTORICAL.isoformat()

        submission = {
            "kind": "geo_ml",
            "field_id": "F-03",
            "inputs": {"season": 2026, "source": "historical", "latest": True},
        }
        key = {"Idempotency-Key": "before-field-edit"}
        queued = client.post("/api/jobs", json=submission, headers=key).json()
        assert queued["boundary_revision"] == 0
        field = next(item for item in client.get("/api/fields").json() if item["id"] == "F-03")
        original_geometry = field["geometry"]
        shifted = {
            "type": "Polygon",
            "coordinates": [
                [[lon + 0.0001, lat] for lon, lat in original_geometry["coordinates"][0]]
            ],
        }
        path = "/api/fields/F-03/boundary"
        changed = client.patch(path, json={"geometry": shifted, "boundary_revision": 0})
        assert changed.status_code == 200
        assert changed.json()["boundary_revision"] == 1
        assert client.get(endpoint).json() is None

        process_job(database, queued["id"], lease_seconds=135, now=TODAY)
        stale = client.get(f"/api/jobs/{queued['id']}").json()
        assert stale["status"] == "failed" and stale["attempts"] == 1
        assert stale["result"] is None
        assert "boundary changed" in stale["error"]
        assert client.post("/api/jobs", json=submission, headers=key).json()["id"] == queued["id"]

        # Returning to the same polygon still needs jobs for the new revision.
        restored = client.patch(path, json={"geometry": original_geometry, "boundary_revision": 1})
        assert restored.json()["boundary_revision"] == 2
        assert client.get(endpoint).json() is None
        assert refresh.enqueue_refresh(database, now=TODAY) == 2
        run_queue(database, TODAY)
        assert client.get(endpoint).json()["date"] == LATEST_HISTORICAL.isoformat()


def test_scheduled_estimate_survives_newer_failures_and_backfills(
    database: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(create_app(session_factory=database)) as client:
        endpoint = "/api/fields/F-03/moisture?season=2026"
        assert client.get(endpoint + "&source=ml").json() is None
        assert client.get(endpoint + "&source=historical").json() is None
        assert client.get(endpoint).json()["demo"]
        assert client.get(endpoint + "&source=demo").json()["demo"]
        assert refresh.enqueue_refresh(database, now=TODAY) == 6
        assert refresh.enqueue_refresh(database, now=TODAY + timedelta(hours=2)) == 0
        run_queue(database, TODAY)

        layer = client.get(endpoint + "&source=ml").json()
        assert layer["date"] == LATEST.isoformat()
        assert layer["depth_cm"] == [5, 5]
        assert layer["source"]["model_version"] == "test-model"
        assert layer["source"]["interval"] == {
            "lower": 0.23,
            "upper": 0.43,
            "coverage": 0.9,
            "empirical_coverage": 0.82,
        }
        assert layer["cells"][0]["value"] == pytest.approx(0.33)
        assert client.get(endpoint).json()["source"]["kind"] == "reanalysis"

        older = client.post(
            "/api/jobs",
            json={
                "kind": "geo_ml",
                "field_id": "F-03",
                "inputs": {"season": 2026, "source": "ml", "sample_date": "2026-08-01"},
            },
        )
        assert older.status_code == 202
        run_queue(database, TODAY + timedelta(seconds=1))
        assert (
            client.get(f"/api/jobs/{older.json()['id']}").json()["result"]["date"] == "2026-08-01"
        )
        assert client.get(endpoint + "&source=ml").json() == layer
        assert (
            client.get(endpoint + "&source=ml&sample_date=2026-08-01").json()["date"]
            == "2026-08-01"
        )
        assert client.get("/api/fields/F-03/moisture?source=ml").json() == layer

        def outage(*_args, **_options):
            raise MoistureSourceError("Archive timeout")

        monkeypatch.setattr(ml_layer, "fetch_moisture_features", outage)
        tomorrow = TODAY + timedelta(days=1)
        assert refresh.enqueue_refresh(database, now=tomorrow) == 6
        run_queue(database, tomorrow)
        assert client.get(endpoint + "&source=ml").json() == layer
        with database() as session:
            new_jobs = list(session.scalars(select(Job).where(Job.status == "retrying")))
            assert len(new_jobs) == 3
            assert all(job.inputs["source"] == "ml" for job in new_jobs)


def test_refresh_replays_exhausted_transient_failures_with_backoff_until_recovered(
    database: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    fetch_features = ml_layer.fetch_moisture_features

    def outage(*_args, **_options):
        raise MoistureSourceError("Archive timeout")

    monkeypatch.setattr(ml_layer, "fetch_moisture_features", outage)
    assert refresh.enqueue_refresh(database, now=TODAY) == 6
    for seconds in (0, 2, 6):
        run_queue(database, TODAY + timedelta(seconds=seconds))
    exhausted_at = TODAY + timedelta(seconds=6)
    assert refresh.enqueue_refresh(database, now=exhausted_at + timedelta(hours=1)) == 0
    replay_at = exhausted_at + refresh.REPLAY_AFTER
    assert refresh.enqueue_refresh(database, now=replay_at) == 3
    assert refresh.enqueue_refresh(database, now=replay_at + timedelta(hours=1)) == 0
    for seconds in (0, 2, 6):
        run_queue(database, replay_at + timedelta(seconds=seconds))
    exhausted_at = replay_at + timedelta(seconds=6)
    assert refresh.enqueue_refresh(database, now=exhausted_at + timedelta(hours=3)) == 0
    second_replay_at = exhausted_at + timedelta(hours=4)
    assert refresh.enqueue_refresh(database, now=second_replay_at) == 3
    monkeypatch.setattr(ml_layer, "fetch_moisture_features", fetch_features)
    run_queue(database, second_replay_at)
    assert refresh.enqueue_refresh(database, now=second_replay_at + timedelta(hours=8)) == 0
    with database() as session:
        jobs = list(session.scalars(select(Job)))
        assert sum(job.status == "failed" for job in jobs) == 6
        assert sum(job.status == "succeeded" for job in jobs) == 6

    with TestClient(create_app(session_factory=database)) as client:
        assert (
            client.get("/api/fields/F-03/moisture?source=ml").json()["date"] == LATEST.isoformat()
        )
        reserved = client.post(
            "/api/jobs",
            headers={"Idempotency-Key": "internal:moisture:test"},
            json={"kind": "geo_ml", "field_id": "F-03", "inputs": {"season": 2026, "source": "ml"}},
        )
        assert reserved.status_code == 400


def test_automatic_run_uses_last_thawed_day_and_records_actual_date(
    database: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fetch_frozen_latest(_lat, _lon, start, end, *, api_key):
        return feature_series(start, end, frozen=end)

    monkeypatch.setattr(ml_layer, "fetch_moisture_features", fetch_frozen_latest)
    assert refresh.enqueue_refresh(database, now=TODAY) == 6
    run_queue(database, TODAY)
    with TestClient(create_app(session_factory=database)) as client:
        assert client.get("/api/fields/F-03/moisture?source=ml").json()["date"] == "2026-08-01"
        requested = client.post(
            "/api/jobs",
            json={
                "kind": "geo_ml",
                "field_id": "F-03",
                "inputs": {"season": 2026, "source": "ml", "sample_date": LATEST.isoformat()},
            },
        )
        assert requested.status_code == 202
        run_queue(database, TODAY)
        status = client.get(f"/api/jobs/{requested.json()['id']}").json()
        assert status["status"] == "failed"
        assert status["attempts"] == 1
        assert "unfrozen ground" in status["error"]


def test_new_refresh_excludes_fields_outside_supported_geography(
    database: sessionmaker[Session],
) -> None:
    with TestClient(create_app(session_factory=database)) as client:
        field = client.post(
            "/api/fields",
            json={
                "name": "Outside",
                "crop": "Wheat",
                "area_ha": 12,
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[30.4, 50.2], [30.5, 50.2], [30.5, 50.3], [30.4, 50.2]]],
                },
            },
        )
        assert field.status_code == 201
        assert refresh.enqueue_refresh(database, now=TODAY) == 7
        outside = field.json()["id"]
        job = client.post(
            "/api/jobs",
            json={
                "kind": "geo_ml",
                "field_id": outside,
                "inputs": {"season": 2026, "source": "ml", "sample_date": "2026-08-01"},
            },
        )
        assert job.status_code == 202
        run_queue(database, TODAY + timedelta(seconds=1))
        state = client.get(f"/api/jobs/{job.json()['id']}").json()
        assert state["status"] == "failed"
        assert state["attempts"] == 1
        assert "outside the validated" in state["error"]


def test_latest_archive_day_uses_submission_date_and_handles_season_boundary() -> None:
    new_year = datetime(2027, 1, 3, tzinfo=UTC)

    def supported(_day):
        return True

    assert ml_layer.resolve_ml_day(2026, None, new_year, supported) == date(2026, 12, 28)
    assert ml_layer.resolve_ml_day(2026, None, new_year + timedelta(days=5), supported) == date(
        2026, 12, 31
    )
    assert ml_layer.resolve_ml_day(2026, None, TODAY, supported) == LATEST
    with pytest.raises(ValueError, match="selected season"):
        ml_layer.resolve_ml_day(2027, None, new_year, supported)
    with pytest.raises(ValueError, match="six days"):
        ml_layer.resolve_ml_day(2026, date(2026, 8, 3), TODAY, supported)


def test_latest_only_uses_months_with_held_out_support() -> None:
    def supported(day):
        return 4 <= day.month <= 10

    winter = datetime(2026, 11, 30, tzinfo=UTC)
    assert ml_layer.resolve_ml_day(2026, None, winter, supported) == date(2026, 10, 31)
    with pytest.raises(ValueError, match="does not cover this month"):
        ml_layer.resolve_ml_day(2026, date(2026, 11, 1), winter, supported)


def test_latest_live_layer_rolls_over_without_changing_historical_season(
    database: sessionmaker[Session],
) -> None:
    next_year = datetime(2027, 8, 8, 1, tzinfo=UTC)
    for at in (TODAY, next_year):
        assert refresh.enqueue_refresh(database, now=at) == 6
        run_queue(database, at)
    with TestClient(create_app(session_factory=database)) as client:
        endpoint = "/api/fields/F-03/moisture"
        assert client.get(endpoint + "?source=ml").json()["date"] == "2027-08-02"
        assert client.get(endpoint + "?source=historical").json()["date"] == "2027-08-03"
        assert client.get(endpoint + "?season=2026&source=ml").json()["date"] == "2026-08-02"


@pytest.mark.parametrize(
    ("at", "previous", "latest"),
    [
        (TODAY, LATEST, LATEST_HISTORICAL),
        (datetime(2027, 1, 6, 1, tzinfo=UTC), date(2026, 12, 31), date(2027, 1, 1)),
    ],
)
def test_archive_refresh_updates_when_newest_full_day_is_published(
    database: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    at: datetime,
    previous: date,
    latest: date,
) -> None:
    published = False

    def archive(url):
        data = raw_archive(url)
        if not published:
            data["hourly"][VARIABLE][-1] = None
        return data

    monkeypatch.setattr(service, "HISTORICAL_GEO_MODEL", ERA5LandMoistureModel(archive))
    assert refresh.enqueue_refresh(database, now=at) == 6
    run_queue(database, at)
    with TestClient(create_app(session_factory=database)) as client:
        path = "/api/fields/F-03/moisture?source=historical"
        prior_sample = client.get(path).json()
        assert prior_sample["date"] == str(previous)
        assert prior_sample["season"] == previous.year
        assert client.get(f"{path}&season={previous.year}&sample_date={previous}").json() == (
            prior_sample
        )
        if previous.year != latest.year:
            assert client.get(f"{path}&season={latest.year}").json() is None
        assert refresh.enqueue_refresh(database, now=at + timedelta(hours=11)) == 0
        published = True
        replay_at = at + refresh.ARCHIVE_RECHECK_AFTER
        assert refresh.enqueue_refresh(database, now=replay_at) == 3
        run_queue(database, replay_at)
        newest_sample = client.get(path).json()
        assert newest_sample["date"] == str(latest)
        assert newest_sample["season"] == latest.year
        assert client.get(f"{path}&season={previous.year}&sample_date={previous}").json() == (
            prior_sample
        )
        assert refresh.enqueue_refresh(database, now=replay_at + timedelta(hours=8)) == 0


def test_unpromoted_model_keeps_only_raw_auto_refresh_until_enabled(
    database: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    model = refresh.get_moisture_model()
    model.promoted = False
    monkeypatch.setattr(refresh, "get_moisture_model", lambda: model)
    monkeypatch.setenv("MOISTURE_ENABLE_EXPERIMENTAL", "0")
    assert refresh.enqueue_refresh(database, now=TODAY) == 3
    run_queue(database, TODAY)
    with TestClient(create_app(session_factory=database)) as client:
        assert client.get("/api/fields/F-03/moisture?source=ml").json() is None
        assert client.get("/api/fields/F-03/moisture?source=historical").json()["date"] == str(
            LATEST_HISTORICAL
        )
        result = client.post(
            "/api/jobs",
            json={"kind": "geo_ml", "field_id": "F-03", "inputs": {"season": 2026, "source": "ml"}},
        ).json()
        run_queue(database, TODAY)
        assert client.get(f"/api/jobs/{result['id']}").json()["status"] == "failed"

        monkeypatch.setenv("MOISTURE_ENABLE_EXPERIMENTAL", "1")
        assert refresh.enqueue_refresh(database, now=TODAY) == 3
        run_queue(database, TODAY)
        layer = client.get("/api/fields/F-03/moisture?source=ml").json()
        assert layer["source"]["experimental"] is True
        assert layer["date"] == str(LATEST)
        monkeypatch.setenv("MOISTURE_ENABLE_EXPERIMENTAL", "0")
        assert client.get("/api/fields/F-03/moisture?source=ml").json() is None
