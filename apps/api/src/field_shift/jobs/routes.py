from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query
from sqlalchemy import select

from field_shift.db import SessionDep
from field_shift.errors import ConflictError
from field_shift.jobs import repository
from field_shift.jobs.models import Job
from field_shift.jobs.schemas import JobResponse, JobSubmission
from field_shift.modules.farm.models import FARM_ID
from field_shift.modules.fields.models import Field
from field_shift.modules.fields.service import require_field

router = APIRouter(prefix="/jobs", tags=["jobs"])
IdempotencyHeader = Annotated[
    str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[!-~]+$")
]
FieldFilter = Annotated[str | None, Query(min_length=1, max_length=64)]
PageLimit = Annotated[int, Query(ge=1, le=100)]


@router.post("", response_model=JobResponse, status_code=202)
def create_job(
    body: JobSubmission,
    session: SessionDep,
    idempotency_key: IdempotencyHeader = None,
    geometry_revision: Annotated[int | None, Query(ge=0)] = None,
) -> Job:
    if idempotency_key is not None and idempotency_key.startswith(repository.INTERNAL_KEY_PREFIX):
        raise HTTPException(status_code=400, detail="This idempotency key prefix is reserved.")
    field = require_field(session, body.field_id)
    if geometry_revision is not None and geometry_revision != field.boundary_revision:
        raise ConflictError("Field boundary changed. Reload the field before starting a run.")
    if body.inputs.analysis == "vegetation":
        from field_shift.modules.imagery.vegetation import vegetation_footprint

        _, fingerprint = vegetation_footprint(session, field, body.inputs.season)
        body.inputs.vegetation_mask_hash = fingerprint
    try:
        job = repository.submit(
            session,
            kind=body.kind,
            field_id=body.field_id,
            inputs=body.inputs.stored(),
            boundary_revision=field.boundary_revision,
            idempotency_key=idempotency_key,
        )
    except repository.IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return job


@router.get("", response_model=list[JobResponse])
def list_jobs(
    session: SessionDep, field_id: FieldFilter = None, limit: PageLimit = 25
) -> list[Job]:
    if field_id is not None:
        require_field(session, field_id)
    statement = select(Job).join(Field, Field.id == Job.field_id).where(Field.farm_id == FARM_ID)
    if field_id is not None:
        statement = statement.where(Job.field_id == field_id)
    return list(
        session.scalars(statement.order_by(Job.created_at.desc(), Job.id.desc()).limit(limit))
    )


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: UUID, session: SessionDep) -> Job:
    job = session.get(Job, str(job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    require_field(session, job.field_id)
    return job
