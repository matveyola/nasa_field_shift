"""Acquire once, checkpoint under lease, replay offline, fence final field scope.

Lock order is Field then Job for both checkpoint and final result. Each private
phase has a 50s process bound /55s HTTP timeout, below the existing120s task bound.
Existing3 attempts,135s lease, late ack and dispatcher recovery remain unchanged.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select

from field_shift.jobs import repository
from field_shift.jobs.models import Job, JobStatus
from field_shift.jobs.settings import get_job_settings
from field_shift.jobs.worker import InvalidModelInput, ModelDataUnavailable
from field_shift.modules.fields.models import Field

from .land import land_status
from .schemas import HandoffResult
from .service import digest
from .transport import HandoffClient, HandoffInvalid, HandoffUnavailable


def current_field(session, lease, *, lock=False):
    statement = select(Field).where(Field.id == lease.field_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    field = session.scalar(statement)
    if (
        field is None
        or field.farm_id != lease.inputs["farm_id"]
        or field.boundary_revision != lease.boundary_revision
        or digest(field.geometry) != lease.inputs["geometry_sha256"]
    ):
        raise InvalidModelInput("Field owner or boundary changed; submit a new comparison")
    current_land = land_status(session, field)
    if current_land not in {"arable", "unknown"} or (
        lease.inputs.get("land_status") == "arable" and current_land != "arable"
    ):
        raise InvalidModelInput("Current sourced profile no longer supports arable land")
    return field


def checkpoint(factory, lease, evidence):
    with factory.begin() as session:
        current_field(session, lease, lock=True)
        job = session.scalar(select(Job).where(Job.id == lease.job_id).with_for_update())
        at = (
            session.scalar(select(func.clock_timestamp()))
            if session.get_bind().dialect.name == "postgresql"
            else datetime.now(UTC)
        )
        expiry = job.lease_expires_at if job else None
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        if (
            job is None
            or job.status != JobStatus.RUNNING
            or job.attempt_token != lease.token
            or expiry is None
            or expiry <= at
        ):
            raise InvalidModelInput("The model acquisition attempt is no longer current")
        job.result = {"phase": "acquired", "evidence": evidence}


def validate_snapshot(lease, snapshot):
    if (
        snapshot.get("operation") != lease.inputs["operation"]
        or snapshot.get("parameters") != lease.inputs["parameters"]
        or snapshot.get("land_status") != lease.inputs.get("land_status", "unknown")
    ):
        raise HandoffInvalid("Acquired evidence differs from the bound field request")


def execute(factory, lease, client=None):
    if get_job_settings().task_time_limit_seconds < 115:
        raise InvalidModelInput(
            "Rotation execution requires a worker task bound of at least 115 seconds"
        )
    client = client or HandoffClient()
    with factory() as session:
        current_field(session, lease)
        job = session.get(Job, lease.job_id)
        snapshot = (job.result or {}).get("evidence")
    try:
        if snapshot is None:
            snapshot = client.call(
                "acquire",
                {
                    "operation": lease.inputs["operation"],
                    "parameters": lease.inputs["parameters"],
                    "allow_public_data": lease.inputs["allow_public_data"],
                    "land_status": lease.inputs.get("land_status", "unknown"),
                    "decision_date": datetime.now(UTC).date().isoformat(),
                },
            )
            validate_snapshot(lease, snapshot)
            checkpoint(factory, lease, snapshot)
        validate_snapshot(lease, snapshot)
        envelope = HandoffResult.model_validate(client.call("evaluate", snapshot)).model_dump(
            mode="json"
        )
        if (
            envelope["evidence_sha256"] != snapshot["evidence_sha256"]
            or envelope["operation"] != lease.inputs["operation"]
            or envelope["input_sha256"] != snapshot["input_sha256"]
            or envelope["engine_version"] != snapshot["engine_version"]
        ):
            raise HandoffInvalid("Model response does not match frozen request evidence")
        return {"envelope": envelope, "evidence": snapshot}
    except HandoffInvalid as error:
        raise InvalidModelInput(str(error)) from error
    except HandoffUnavailable as error:
        raise ModelDataUnavailable(str(error)) from error


def succeed(session, lease, result, *, now=None):
    try:
        current_field(session, lease, lock=True)
    except InvalidModelInput as error:
        return repository.fail(session, lease, str(error), retryable=False, now=now)
    return repository.succeed(session, lease, result, now=now)
