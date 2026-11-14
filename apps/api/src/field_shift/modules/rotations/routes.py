from fastapi import APIRouter

from field_shift.db import SessionDep
from field_shift.modules.fields.schemas import Season
from field_shift.modules.rotations import service
from field_shift.modules.rotations.planning_routes import router as planning_router
from field_shift.modules.rotations.schemas import (
    MainPlanUpdate,
    PlanId,
    PlanUpdate,
    RotationRead,
    RotationYieldRead,
)
from field_shift.modules.rotations.yield_service import get_yield_comparison
from field_shift.modules.weather.scenarios import Scenario

router = APIRouter(prefix="/rotations", tags=["rotations"])
router.include_router(planning_router)


@router.get("", response_model=RotationRead)
def rotations(
    field_id: str,
    session: SessionDep,
    season: Season = 2026,
    scenario: Scenario = Scenario.DRY_YEAR,
):
    return service.get_rotations(session, field_id, season, scenario)


@router.get("/yield", response_model=RotationYieldRead)
def rotation_yield(field_id: str, session: SessionDep, season: Season = 2026) -> RotationYieldRead:
    return get_yield_comparison(session, field_id, season)


@router.patch("/main", response_model=MainPlanUpdate)
def patch_main(payload: MainPlanUpdate, session: SessionDep):
    return service.set_main(session, payload)


@router.put("/plans/{plan_id}", response_model=PlanUpdate)
def put_plan(plan_id: PlanId, payload: PlanUpdate, session: SessionDep):
    return service.save_plan(session, plan_id, payload)
