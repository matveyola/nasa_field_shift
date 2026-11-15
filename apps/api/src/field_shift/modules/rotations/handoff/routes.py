"""Thin authenticated bounded routes; calculations execute only in durable workers."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from field_shift.db import SessionDep
from field_shift.modules.access.dependencies import require_principal

from . import service
from .schemas import HandoffCatalog, HandoffJob, HandoffSubmission, SeasonalWater

router = APIRouter(prefix="/rotations/handoff", tags=["rotation-handoff"])


async def bounded_body(request: Request):
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > 70_000:
            raise HTTPException(413, "Rotation request exceeds byte bound")
    try:
        return HandoffSubmission.model_validate_json(raw)
    except (ValidationError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(
            422, "Invalid rotation comparison request: " + str(error)[:500]
        ) from error


@router.get("/catalog", response_model=HandoffCatalog)
def catalog(principal=Depends(require_principal)):
    return service.catalog()


@router.post("/fields/{field_id}/jobs", response_model=HandoffJob, status_code=202)
def submit(
    field_id: str,
    session: SessionDep,
    body=Depends(bounded_body),
    principal=Depends(require_principal),
):
    result = service.submit(session, principal, field_id, body)
    session.commit()
    return result


@router.get("/fields/{field_id}/jobs/{job_id}", response_model=HandoffJob)
def read(field_id: str, job_id: str, session: SessionDep, principal=Depends(require_principal)):
    return service.read(session, principal, field_id, job_id)


@router.get("/fields/{field_id}/seasonal-water", response_model=SeasonalWater)
def seasonal_water(
    field_id: str, session: SessionDep, limit: int = 20, principal=Depends(require_principal)
):
    from .water import seasonal_water as read_water

    if not 1 <= limit <= 20:
        raise HTTPException(422, "Choose one to twenty saved jobs")
    return read_water(session, principal, field_id, limit)


@router.get("/fields/{field_id}/jobs", response_model=list[HandoffJob])
def list_saved(
    field_id: str, session: SessionDep, limit: int = 20, principal=Depends(require_principal)
):
    if not 1 <= limit <= 20:
        raise HTTPException(422, "Choose one to twenty saved comparisons")
    return service.list_saved(session, principal, field_id, limit)
