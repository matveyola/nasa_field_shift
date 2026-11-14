import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Barrier
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from field_shift_science.geo_ml.forecast_cache import ForecastCache
from redis import Redis
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from field_shift.api import create_app
from field_shift.config import Settings
from field_shift.db import build_session_factory
from field_shift.jobs import repository
from field_shift.jobs.dispatcher import publish_celery, run_once
from field_shift.jobs.models import Job, JobStatus
from field_shift.jobs.settings import JobRuntimeSettings
from field_shift.modules.geo_ml import refresh

pytestmark = pytest.mark.integration
API_DIR = Path(__file__).resolve().parents[2]
PAYLOAD = {
    "kind": "simulation",
    "field_id": "F-03",
    "inputs": {"season": 2026, "scenario": "dry_year", "irrigation_mm": 0},
}


@pytest.fixture
def database(monkeypatch):
    source_url = os.environ.get("FIELD_SHIFT_TEST_DATABASE_URL")
    if not source_url:
        pytest.skip("Set FIELD_SHIFT_TEST_DATABASE_URL for PostgreSQL integration tests.")

    url = make_url(source_url)
    assert url.get_backend_name() == "postgresql"
    schema = f"fs_test_{uuid4().hex}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    test_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    database_url = test_url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", database_url)
    factory = build_session_factory(database_url)
    try:
        config = Config(API_DIR / "alembic.ini")
        config.set_main_option("script_location", str(API_DIR / "migrations"))
        command.upgrade(config, "head")
        subprocess.run(
            [sys.executable, "-m", "field_shift.seed"], cwd=API_DIR, check=True, timeout=20
        )
        yield database_url, factory
    finally:
        factory.kw["bind"].dispose()
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


@pytest.fixture
def client(database):
    database_url, _ = database
    with TestClient(create_app(Settings(database_url=database_url))) as session:
        yield session


def submit(client, key):
    response = client.post("/api/jobs", json=PAYLOAD, headers={"Idempotency-Key": key})
    assert response.status_code == 202, response.text
    return response.json()


def test_forecast_cache_coalesces_loads_between_redis_clients():
    redis_url = os.environ.get("FIELD_SHIFT_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("Set FIELD_SHIFT_TEST_REDIS_URL for the shared forecast cache check.")

    barrier = Barrier(2)
    identity = f"test-forecast:{uuid4()}"
    requests = []
    payload = {"daily": {"time": ["2026-11-08"]}}

    def fetch():
        requests.append(identity)
        sleep(0.5)
        return payload

    def load(_):
        with Redis.from_url(redis_url) as redis:
            barrier.wait(timeout=5)
            return ForecastCache(redis).get_or_load(identity, fetch, cacheable=lambda _: True)

    try:
        with ThreadPoolExecutor(2) as pool:
            assert list(pool.map(load, range(2))) == [payload, payload]
        assert requests == [identity]
    finally:
        fingerprint = sha256(identity.encode()).hexdigest().encode()
        with Redis.from_url(redis_url) as redis:
            for key in redis.scan_iter(match="field-shift:ifs-input:*"):
                if fingerprint in (redis.get(key) or b""):
                    redis.delete(key)


def test_concurrent_submissions_and_worker_claims_are_atomic(database, client):
    _, factory = database
    barrier = Barrier(2)
    key = uuid4().hex

    def concurrent_submit():
        barrier.wait(timeout=5)
        return submit(client, key)["id"]

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(concurrent_submit) for _ in range(2)]
        ids = [future.result(timeout=15) for future in futures]
    assert ids[0] == ids[1]

    conflict = client.post(
        "/api/jobs",
        json=PAYLOAD | {"kind": "geo_ml"},
        headers={"Idempotency-Key": key},
    )
    assert conflict.status_code == 409

    def concurrent_claim():
        with factory.begin() as session:
            barrier.wait(timeout=5)
            return repository.claim(session, ids[0], lease_seconds=30)

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(concurrent_claim) for _ in range(2)]
        leases = [future.result(timeout=15) for future in futures]
    assert sum(lease is not None for lease in leases) == 1
    assert client.get(f"/api/jobs/{ids[0]}").json()["attempts"] == 1


def test_replicated_moisture_refresh_deduplicates_scheduled_jobs(database, monkeypatch):
    monkeypatch.setenv("MOISTURE_FORECAST_REFRESH", "1")
    monkeypatch.setenv("WATER_FORECAST_REFRESH", "1")
    monkeypatch.setattr(refresh, "active_root_zone_correction", lambda: None)
    _, factory = database
    model = SimpleNamespace(
        promoted=True,
        version="test-v1",
        supports_date=lambda _day: True,
        supports_location=lambda _latitude, _longitude: True,
    )
    monkeypatch.setattr(refresh, "get_moisture_model", lambda: model)
    barrier = Barrier(2)
    at = datetime(2026, 11, 9, tzinfo=UTC)

    def enqueue():
        barrier.wait(timeout=5)
        refresh.enqueue_refresh(factory, now=at)

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(enqueue) for _ in range(2)]
        for future in futures:
            future.result(timeout=15)

    assert refresh.enqueue_refresh(factory, now=at) == 0
    with factory() as session:
        scheduled = list(session.scalars(select(Job)))
    assert {job.field_id for job in scheduled} == {"F-01", "F-02", "F-03"}
    assert len(scheduled) == 12
    assert sum(job.kind == "geo_ml" for job in scheduled) == 9
    assert sum(job.inputs["source"] == "ml" for job in scheduled) == 3
    assert sum(job.inputs["source"] == "historical" for job in scheduled) == 3
    assert sum(job.inputs["source"] == "forecast" for job in scheduled) == 6
    assert sum(job.analysis == "water_forecast" for job in scheduled) == 3


def test_lost_delivery_and_expired_worker_are_recoverable(database, client):
    _, factory = database
    job_id = submit(client, uuid4().hex)["id"]
    instant = datetime.now(UTC) + timedelta(seconds=1)
    settings = JobRuntimeSettings(dispatch_retry_seconds=1)

    def broker_down(_delivery):
        raise ConnectionError("Simulated broker outage before publication")

    cycle = run_once(factory, broker_down, settings=settings, now=instant)
    assert (cycle.reserved, cycle.published) == (1, 0)
    first_queue = []
    cycle = run_once(
        factory, first_queue.append, settings=settings, now=instant + timedelta(seconds=1)
    )
    assert cycle.published == 1
    assert first_queue[0].job_id == job_id

    # Broker accepted publication and subsequently lost its in-memory queue.
    first_queue.clear()
    second_queue = []
    clock = instant + timedelta(seconds=3)
    assert run_once(factory, second_queue.append, settings=settings, now=clock).published == 1
    assert second_queue[0].job_id == job_id
    with factory.begin() as session:
        stale = repository.claim(session, job_id, lease_seconds=2, now=clock)
    assert stale is not None

    clock += timedelta(seconds=2)
    cycle = run_once(factory, second_queue.append, settings=settings, now=clock)
    assert cycle.recovered == 1
    assert cycle.published == 0
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "retrying"

    clock += timedelta(seconds=2)
    assert run_once(factory, second_queue.append, settings=settings, now=clock).published == 1
    with factory.begin() as session:
        current = repository.claim(session, job_id, lease_seconds=10, now=clock)
        assert current is not None
        assert not repository.succeed(session, stale, {"stale": True}, now=clock)
        assert repository.succeed(session, current, {"current": True}, now=clock)
    with factory() as session:
        completed = session.get(Job, job_id)
        assert completed.status == JobStatus.SUCCEEDED
        assert completed.attempts == 2
        assert completed.result == {"current": True}


def test_real_redis_queue_loss_then_celery_delivery(database, client, monkeypatch, tmp_path):
    redis_url = os.environ.get("FIELD_SHIFT_TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("Set FIELD_SHIFT_TEST_REDIS_URL for the Celery transport check.")

    from redis import Redis

    broker = Redis.from_url(redis_url)
    assert broker.ping()
    assert broker.dbsize() == 0, "Use a dedicated, empty Redis database for this test."
    monkeypatch.setenv("REDIS_URL", redis_url)
    _, factory = database
    job_id = submit(client, uuid4().hex)["id"]
    geo_response = client.post("/api/jobs", json=PAYLOAD | {"kind": "geo_ml"})
    assert geo_response.status_code == 202
    geo_id = geo_response.json()["id"]

    settings = JobRuntimeSettings(dispatch_retry_seconds=1)
    instant = datetime.now(UTC) + timedelta(seconds=1)
    assert run_once(factory, publish_celery, settings=settings, now=instant).published == 2
    assert broker.llen("simulation") == broker.llen("geo_ml") == 1
    assert broker.delete("simulation", "geo_ml") == 2
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "queued"
    assert client.get(f"/api/jobs/{geo_id}").json()["status"] == "queued"

    log_path = tmp_path / "worker.log"
    with log_path.open("wb") as log:
        worker = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "field_shift.jobs.celery_app:app",
                "worker",
                "-Q",
                "simulation,geo_ml",
                "--pool=solo",
                "--loglevel=INFO",
                "--without-gossip",
                "--without-mingle",
                "--without-heartbeat",
            ],
            cwd=API_DIR,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            cycle = run_once(
                factory, publish_celery, settings=settings, now=instant + timedelta(seconds=1)
            )
            assert cycle.published == 2
            deadline = monotonic() + 20
            while monotonic() < deadline and worker.poll() is None:
                result = client.get(f"/api/jobs/{job_id}").json()
                geo = client.get(f"/api/jobs/{geo_id}").json()
                if result["status"] == geo["status"] == "succeeded":
                    assert result["attempts"] == 1
                    assert result["result"]["field_id"] == "F-03"
                    assert result["result"]["demo"] is True
                    assert geo["attempts"] == 1
                    assert geo["result"]["demo"] is True
                    break
                sleep(0.1)
            else:
                pytest.fail(log_path.read_text(encoding="utf-8", errors="replace"))
        finally:
            worker.terminate()
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
            broker.close()
