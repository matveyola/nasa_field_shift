"""Thin authenticated bounded routes; calculations execute only in durable workers."""

import json
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ValidationError

from field_shift.db import SessionDep
from field_shift.modules.access.dependencies import require_principal

from . import service, strategy
from .schemas import HandoffCatalog, HandoffJob, HandoffSubmission, SeasonalWater
from .strategy_schemas import SavedStrategy, SaveStrategy, StrategyContext, StrategyRun

router = APIRouter(prefix="/rotations/handoff", tags=["rotation-handoff"])
Submission = TypeVar("Submission", bound=BaseModel)


async def bounded_json(request: Request, schema: type[Submission]) -> Submission:
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > 70_000:
            raise HTTPException(413, "Rotation request exceeds byte bound")
    try:
        return schema.model_validate_json(raw)
    except (ValidationError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(
            422, "Invalid rotation comparison request: " + str(error)[:500]
        ) from error


async def bounded_body(request: Request) -> HandoffSubmission:
    return await bounded_json(request, HandoffSubmission)


async def strategy_body(request: Request) -> StrategyRun:
    return await bounded_json(request, StrategyRun)


async def save_body(request: Request) -> SaveStrategy:
    return await bounded_json(request, SaveStrategy)


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


@router.get("/fields/{field_id}/strategy-context", response_model=StrategyContext)
def strategy_context(field_id: str, session: SessionDep, principal=Depends(require_principal)):
    return strategy.context(session, principal, field_id)


@router.post("/fields/{field_id}/strategy-jobs", response_model=HandoffJob, status_code=202)
def run_strategy(
    field_id: str,
    session: SessionDep,
    body=Depends(strategy_body),
    principal=Depends(require_principal),
):
    result = strategy.submit(session, principal, field_id, body)
    session.commit()
    return result


@router.get("/fields/{field_id}/strategy-scenarios", response_model=list[SavedStrategy])
def saved_strategies(
    field_id: str,
    session: SessionDep,
    offset: int = Query(default=0, ge=0, le=2_147_483_647),
    limit: int = Query(default=20, ge=1, le=50),
    principal=Depends(require_principal),
):
    return strategy.list_saved(session, principal, field_id, offset=offset, limit=limit)


@router.post("/fields/{field_id}/strategy-scenarios", response_model=SavedStrategy)
def save_strategy(
    field_id: str,
    session: SessionDep,
    body=Depends(save_body),
    principal=Depends(require_principal),
):
    return strategy.save(session, principal, field_id, body)
