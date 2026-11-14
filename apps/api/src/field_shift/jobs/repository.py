from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from field_shift.jobs.models import BOUNDARY_REVISION_INPUT, Job, JobKind, JobStatus

DISPATCHABLE = (JobStatus.QUEUED, JobStatus.RETRYING)
MAX_ATTEMPTS = 3
INTERNAL_KEY_PREFIX = "internal:"
LEASE_EXPIRED_ERROR = "The worker did not complete before its lease expired."


class IdempotencyConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Delivery:
    job_id: str
    kind: JobKind


@dataclass(frozen=True, slots=True)
class Lease:
    job_id: str
    token: str
    kind: JobKind
    field_id: str
    inputs: dict[str, Any]
    submitted_at: datetime

    @property
    def boundary_revision(self) -> int:
        return self.inputs.get(BOUNDARY_REVISION_INPUT, 0)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _database_time(session: Session, now: datetime | None) -> datetime:
    if now is not None:
        return now
    if session.get_bind().dialect.name == "postgresql":
        return session.execute(select(func.clock_timestamp())).scalar_one()
    return datetime.now(UTC)


def submit(
    session: Session,
    *,
    kind: JobKind,
    field_id: str,
    inputs: dict[str, Any],
    boundary_revision: int = 0,
    idempotency_key: str | None = None,
    now: datetime | None = None,
) -> Job:
    at = _database_time(session, now)
    stored_inputs = (
        {**inputs, BOUNDARY_REVISION_INPUT: boundary_revision} if boundary_revision else inputs
    )
    job = Job(
        id=str(uuid4()),
        kind=kind,
        field_id=field_id,
        inputs=stored_inputs,
        idempotency_key=idempotency_key,
        status=JobStatus.QUEUED,
        attempts=0,
        max_attempts=MAX_ATTEMPTS,
        available_at=at,
        dispatch_after=at,
        dispatch_count=0,
        created_at=at,
        updated_at=at,
    )
    if idempotency_key is None:
        session.add(job)
        session.flush()
        return job

    # The unique constraint arbitrates concurrent submissions. The savepoint keeps the
    # transaction usable when another request wins the same idempotency key.
    try:
        with session.begin_nested():
            session.add(job)
            session.flush()
    except IntegrityError:
        existing = session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
        if existing is None:
            raise
        existing_options = {
            key: value for key, value in existing.inputs.items() if key != BOUNDARY_REVISION_INPUT
        }
        if (existing.kind, existing.field_id, existing_options) != (kind, field_id, inputs):
            raise IdempotencyConflict(
                "This idempotency key belongs to a different request."
            ) from None
        return existing
    return job


def list_jobs(session: Session, *, field_id: str | None, limit: int) -> list[Job]:
    statement = select(Job).order_by(Job.created_at.desc(), Job.id.desc()).limit(limit)
    if field_id is not None:
        statement = statement.where(Job.field_id == field_id)
    return list(session.scalars(statement))


def reserve_deliveries(
    session: Session,
    *,
    retry_seconds: float,
    limit: int = 20,
    now: datetime | None = None,
) -> list[Delivery]:
    at = _database_time(session, now)
    jobs = session.scalars(
        select(Job)
        .where(
            Job.status.in_(DISPATCHABLE),
            Job.available_at <= at,
            Job.dispatch_after <= at,
        )
        .order_by(Job.dispatch_after, Job.created_at, Job.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    deliveries = []
    for job in jobs:
        deliveries.append(Delivery(job.id, JobKind(job.kind)))
        # Even an apparently successful publish can disappear when the broker loses data.
        # Back off while unclaimed to limit duplicates, with a cap on recovery delay.
        delay = retry_seconds * 2 ** min(job.dispatch_count, 4)
        job.dispatch_after = at + timedelta(seconds=delay)
        job.dispatch_count = min(job.dispatch_count + 1, 4)
    return deliveries


def claim(
    session: Session,
    job_id: str,
    *,
    lease_seconds: int,
    now: datetime | None = None,
) -> Lease | None:
    job = _lock_job(session, job_id)
    if job is None or job.status not in DISPATCHABLE:
        return None
    at = _database_time(session, now)
    if _aware(job.available_at) > at:
        return None

    token = str(uuid4())
    job.status = JobStatus.RUNNING
    job.attempts += 1
    job.attempt_token = token
    job.lease_expires_at = at + timedelta(seconds=lease_seconds)
    job.dispatch_count = 0
    job.updated_at = at
    return Lease(job.id, token, JobKind(job.kind), job.field_id, job.inputs, _aware(job.created_at))


def succeed(
    session: Session,
    lease: Lease,
    result: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    job = _lock_job(session, lease.job_id)
    at = _database_time(session, now)
    if not _owns_lease(job, lease, at):
        return False
    assert job is not None
    job.status = JobStatus.SUCCEEDED
    job.result = result
    job.error = None
    _release(job, at)
    return True


def fail(
    session: Session,
    lease: Lease,
    error: str,
    *,
    now: datetime | None = None,
    retryable: bool = True,
) -> bool:
    job = _lock_job(session, lease.job_id)
    at = _database_time(session, now)
    if not _owns_lease(job, lease, at):
        return False
    assert job is not None
    _record_failure(job, error, at, retryable=retryable)
    return True


def recover_expired(session: Session, *, limit: int = 100, now: datetime | None = None) -> int:
    at = _database_time(session, now)
    jobs = list(
        session.scalars(
            select(Job)
            .where(Job.status == JobStatus.RUNNING, Job.lease_expires_at <= at)
            .order_by(Job.lease_expires_at, Job.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for job in jobs:
        _record_failure(job, LEASE_EXPIRED_ERROR, at)
    return len(jobs)


def _lock_job(session: Session, job_id: str) -> Job | None:
    return session.scalar(
        select(Job)
        .where(Job.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _owns_lease(job: Job | None, lease: Lease, at: datetime) -> bool:
    return (
        job is not None
        and job.status == JobStatus.RUNNING
        and job.attempt_token == lease.token
        and job.lease_expires_at is not None
        and _aware(job.lease_expires_at) > at
    )


def _release(job: Job, at: datetime) -> None:
    job.attempt_token = None
    job.lease_expires_at = None
    job.updated_at = at


def _record_failure(job: Job, error: str, at: datetime, *, retryable: bool = True) -> None:
    job.error = error
    _release(job, at)
    if not retryable or job.attempts >= job.max_attempts:
        job.status = JobStatus.FAILED
        return

    job.status = JobStatus.RETRYING
    job.available_at = at + timedelta(seconds=2**job.attempts)
    job.dispatch_after = job.available_at
