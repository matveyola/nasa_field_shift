from uuid import UUID

from fastapi import APIRouter

from field_shift.db import SessionDep
from field_shift.modules.rotations import planning_service
from field_shift.modules.rotations.planning_schemas import (
    PlannerSeason,
    PlanningContext,
    PlanningRead,
    PlanningRequest,
    PlanningSummary,
)

router = APIRouter(prefix="/planning", tags=["rotation-planning"])


@router.get("/context", response_model=PlanningContext)
def context(session: SessionDep, start_season: PlannerSeason = 2027):
    return planning_service.planning_context(session, start_season)


@router.get("/drafts", response_model=list[PlanningSummary])
def drafts(session: SessionDep):
    return planning_service.list_drafts(session)


@router.post("/drafts", response_model=PlanningRead, status_code=201)
def create(payload: PlanningRequest, session: SessionDep):
    return planning_service.create_draft(session, payload)


@router.get("/drafts/{draft_id}", response_model=PlanningRead)
def detail(draft_id: UUID, session: SessionDep):
    return planning_service.get_draft(session, str(draft_id))


@router.post("/drafts/{draft_id}/apply", response_model=PlanningRead)
def apply(draft_id: UUID, session: SessionDep):
    return planning_service.apply_draft(session, str(draft_id))
