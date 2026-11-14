from datetime import UTC, datetime, timedelta

import pytest

from field_shift.db import build_session_factory
from field_shift.jobs import repository
from field_shift.jobs.dispatcher import run_once
from field_shift.jobs.models import Job, JobKind, JobStatus
from field_shift.jobs.settings import JobRuntimeSettings
from field_shift.jobs.worker import process_job

START = datetime(2026, 8, 1, tzinfo=UTC)
INPUTS = {"season": 2026, "scenario": "dry_year", "irrigation_mm": 0.0}


@pytest.fixture
def factory():
    sessions = build_session_factory("sqlite://")
    Job.__table__.create(sessions.kw["bind"])
    try:
        yield sessions
    finally:
        sessions.kw["bind"].dispose()


def submit_job(factory, key=None, *, kind=JobKind.SIMULATION, inputs=INPUTS):
    with factory.begin() as session:
        return repository.submit(
            session, kind=kind, field_id="F-03", inputs=inputs, idempotency_key=key, now=START
        ).id


def job_snapshot(factory, job_id):
    with factory() as session:
        job = session.get(Job, job_id)
        assert job is not None
        return job.status, job.attempts, job.result, job.error


def test_submission_is_idempotent_and_payload_conflict_is_rejected(factory):
    original = submit_job(factory, "field-water:one")
    assert submit_job(factory, "field-water:one") == original
    with pytest.raises(repository.IdempotencyConflict):
        submit_job(factory, "field-water:one", inputs={**INPUTS, "irrigation_mm": 15})
    with factory() as session:
        assert [job.id for job in repository.list_jobs(session, field_id="F-03", limit=10)] == [
            original
        ]


def test_rolling_back_submission_discards_idempotency_reservation(factory):
    with factory() as session:
        job_id = repository.submit(
            session,
            kind=JobKind.SIMULATION,
            field_id="F-03",
            inputs=INPUTS,
            idempotency_key="aborted-submission",
            now=START,
        ).id
        session.rollback()
    with factory() as session:
        assert session.get(Job, job_id) is None
    assert submit_job(factory, "aborted-submission") != job_id


def test_broker_failure_and_discarded_delivery_do_not_lose_job(factory):
    job_id = submit_job(factory, kind=JobKind.GEO_ML)
    settings = JobRuntimeSettings(dispatch_retry_seconds=2)
    published_state = []

    def failed_publish(delivery):
        with factory() as session:
            dispatch_count = session.get(Job, job_id).dispatch_count
        published_state.append((delivery.job_id, delivery.kind, dispatch_count))
        raise ConnectionError("Redis unavailable")

    first = run_once(factory, failed_publish, settings=settings, now=START)
    assert (first.reserved, first.published) == (1, 0)
    assert published_state == [(job_id, JobKind.GEO_ML, 1)]
    assert job_snapshot(factory, job_id) == (JobStatus.QUEUED, 0, None, None)

    deliveries = []
    assert not run_once(factory, deliveries.append, settings=settings, now=START).reserved
    second = run_once(
        factory, deliveries.append, settings=settings, now=START + timedelta(seconds=2)
    )
    assert second.published == 1
    third = run_once(
        factory, deliveries.append, settings=settings, now=START + timedelta(seconds=6)
    )
    assert third.published == 1
    assert [delivery.job_id for delivery in deliveries] == [job_id, job_id]


def test_recovery_fences_worker_and_does_not_run_duplicate_delivery(factory):
    job_id = submit_job(factory)
    with factory.begin() as session:
        stale = repository.claim(session, job_id, lease_seconds=5, now=START)
    assert stale is not None

    calls = []

    def handler(_session, lease):
        calls.append(lease.token)
        return {"reserve": 58}

    handlers = {JobKind.SIMULATION: handler}
    process_job(factory, job_id, lease_seconds=5, handlers=handlers, now=START)
    assert not calls

    expires = START + timedelta(seconds=5)
    with factory.begin() as session:
        assert not repository.succeed(session, stale, {"reserve": 0}, now=expires)
        assert repository.recover_expired(session, now=expires) == 1
        assert repository.recover_expired(session, now=expires) == 0
    assert job_snapshot(factory, job_id)[:3] == (JobStatus.RETRYING, 1, None)
    process_job(factory, job_id, lease_seconds=5, handlers=handlers, now=expires)
    assert not calls

    due = expires + timedelta(seconds=2)
    process_job(factory, job_id, lease_seconds=5, handlers=handlers, now=due)
    process_job(factory, job_id, lease_seconds=5, handlers=handlers, now=due)
    assert len(calls) == 1
    assert calls[0] != stale.token
    with factory.begin() as session:
        assert not repository.succeed(session, stale, {"reserve": 0}, now=due)
    assert job_snapshot(factory, job_id) == (JobStatus.SUCCEEDED, 2, {"reserve": 58}, None)


def test_model_errors_stop_after_retry_budget_and_keep_failed_row(factory):
    job_id = submit_job(factory)

    def handler(_session, _lease):
        raise RuntimeError("provider details are for worker logs")

    for elapsed, expected in [
        (0, JobStatus.RETRYING),
        (2, JobStatus.RETRYING),
        (6, JobStatus.FAILED),
    ]:
        process_job(
            factory,
            job_id,
            lease_seconds=5,
            handlers={JobKind.SIMULATION: handler},
            now=START + timedelta(seconds=elapsed),
        )
        assert job_snapshot(factory, job_id)[0] == expected

    assert job_snapshot(factory, job_id) == (JobStatus.FAILED, 3, None, "Processing failed.")
    with factory.begin() as session:
        assert (
            repository.reserve_deliveries(session, retry_seconds=2, now=START + timedelta(hours=1))
            == []
        )


@pytest.mark.parametrize("result", [{"reserve": float("nan")}, [58]])
def test_invalid_provider_output_is_retryable(factory, result):
    job_id = submit_job(factory)
    process_job(
        factory,
        job_id,
        lease_seconds=5,
        handlers={JobKind.SIMULATION: lambda *_: result},
        now=START,
    )
    assert job_snapshot(factory, job_id) == (JobStatus.RETRYING, 1, None, "Processing failed.")
