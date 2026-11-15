"""Field scope, immutable snapshots and durable submission. No scientific execution."""

import hashlib
import json

from fastapi import HTTPException
from field_shift_science.fields.schemas import Polygon
from field_shift_science.geo_ml.era5_land import field_location
from sqlalchemy import select

from field_shift.jobs import repository
from field_shift.jobs.models import Job, JobKind, JobStatus
from field_shift.modules.fields.models import Field

from .catalog_fallback import REFERENCE_POINTS
from .land import land_status
from .schemas import HandoffCatalog, HandoffJob, HandoffResult
from .transport import HandoffClient, HandoffUnavailable


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def scoped_field(session, principal, field_id, *, lock=False):
    statement = select(Field).where(Field.id == field_id, Field.farm_id == principal.farm_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    field = session.scalar(statement)
    if field is None:
        raise HTTPException(404, "Field not found")
    return field


def catalog(client=None):
    from .catalog_fallback import fallback_catalog

    try:
        return HandoffCatalog.model_validate((client or HandoffClient()).call("catalog", {}))
    except HandoffUnavailable as error:
        return HandoffCatalog.model_validate(fallback_catalog(str(error)))


def view(job):
    envelope = (
        job.result.get("envelope") if job.result and job.status == JobStatus.SUCCEEDED else None
    )
    return HandoffJob(
        job_id=job.id,
        field_id=job.field_id,
        geometry_revision=job.boundary_revision,
        status=job.status,
        attempts=job.attempts,
        result=HandoffResult.model_validate(envelope) if envelope else None,
        error=job.error,
    )


def read(session, principal, field_id, job_id):
    scoped_field(session, principal, field_id)
    job = session.get(Job, job_id)
    if (
        job is None
        or job.field_id != field_id
        or job.analysis != "rotation_handoff"
        or job.inputs.get("farm_id") != principal.farm_id
    ):
        raise HTTPException(404, "Rotation job not found")
    return view(job)


def submit(session, principal, field_id, body):
    if principal.role not in {"admin", "editor"}:
        raise HTTPException(403, "Editing permission is required to submit a comparison")
    field = scoped_field(session, principal, field_id, lock=True)
    if field.boundary_revision != body.geometry_revision:
        raise HTTPException(409, "Field boundary changed; reload the field")
    request_hash = digest(body.model_dump(mode="json"))
    key = "internal:rotation-handoff:" + digest([principal.farm_id, field_id, body.request_id])
    existing = session.scalar(select(Job).where(Job.idempotency_key == key))
    if existing is not None:
        if existing.inputs.get("request_sha256") != request_hash:
            raise HTTPException(409, "Request ID belongs to a different comparison")
        return view(existing)
    land = land_status(session, field)
    if land not in {"arable", "unknown"}:
        raise HTTPException(422, "Current sourced profile identifies non-arable, ocean or ice land")
    latitude, longitude = field_location(Polygon.model_validate(field.geometry))
    parameters = dict(body.parameters)
    if body.operation == "forecast":
        parameters["field"] = {
            "id": "registered-" + digest(field.id)[:16],
            "name": field.name,
            "latitude": latitude,
            "longitude": longitude,
        }
    elif body.operation in {"climate_step", "climate_compare"}:
        parameters["field"] = {"name": field.name, "latitude": latitude, "longitude": longitude}
    elif body.operation in {"assessment", "sutherland_2027"}:
        # Reference assessments may never masquerade as predictions for an unrelated farm.
        reference = next(
            (
                key
                for key, (lat, lon) in REFERENCE_POINTS.items()
                if abs(latitude - lat) <= 0.001 and abs(longitude - lon) <= 0.001
            ),
            None,
        )
        if reference is None or (
            body.operation == "sutherland_2027" and reference != "sutherland_nw"
        ):
            raise HTTPException(
                422, "This bundled reference scenario does not match the registered field location"
            )
        if body.operation == "assessment":
            parameters["field_id"] = reference
    else:
        parameters["field_id"] = "registered-" + digest(field.id)[:16]
    inputs = {
        "analysis": "rotation_handoff",
        "farm_id": principal.farm_id,
        "request_sha256": request_hash,
        "geometry_sha256": digest(field.geometry),
        "operation": body.operation,
        "land_status": land,
        "parameters": parameters,
        "allow_public_data": body.allow_public_data,
    }
    try:
        job = repository.submit(
            session,
            kind=JobKind.SIMULATION,
            field_id=field.id,
            inputs=inputs,
            boundary_revision=field.boundary_revision,
            idempotency_key=key,
        )
    except repository.IdempotencyConflict as error:
        raise HTTPException(409, str(error)) from error
    return view(job)


def list_saved(session, principal, field_id, limit=20):
    field = scoped_field(session, principal, field_id)
    jobs = session.scalars(
        select(Job)
        .where(
            Job.field_id == field_id,
            Job.inputs["analysis"].as_string() == "rotation_handoff",
            Job.inputs["farm_id"].as_integer() == principal.farm_id,
            Job.matches_boundary_revision(field.boundary_revision),
        )
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(limit)
    )
    return [view(job) for job in jobs]
