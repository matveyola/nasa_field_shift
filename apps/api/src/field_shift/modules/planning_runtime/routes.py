"""Additive workspace endpoints; frozen planning-v1 payloads remain unchanged."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from field_shift.contracts.planning_v1 import PlanningError
from field_shift.db import SessionDep
from field_shift.modules.access.dependencies import require_principal
from field_shift.modules.fields.planning_profile.service import ProfileError
from field_shift.modules.rotations.scenario_planning.common import reason
from field_shift.modules.rotations.scenario_planning.routes import create_router
from field_shift.modules.rotations.scenario_planning.service import PlanningFailure

from .service import service

router = create_router(service)
workspace_router = APIRouter(prefix="/planning-workspace", tags=["planning-workspace"])


def invoke(session, action):
    try:
        result = action()
        session.commit()
        return result
    except (PlanningFailure, ProfileError) as exc:
        session.rollback()
        return JSONResponse(
            status_code={
                "not_found": 404,
                "forbidden": 403,
                "stale": 409,
                "conflict": 409,
                "unavailable": 503,
                "invalid_input": 422,
            }[exc.code],
            content=PlanningError(
                request_id=None,
                code=exc.code,
                retryable=False,
                reasons=[exc.reason]
                if isinstance(exc, PlanningFailure)
                else [reason(exc.code, exc.message, "profile", "result")],
            ).model_dump(mode="json"),
        )


@workspace_router.get("")
def bootstrap(session: SessionDep, principal=Depends(require_principal)):
    return invoke(session, lambda: service.bootstrap(session, principal))


@workspace_router.post("/fields/{field_id}/initialize")
def initialize(field_id: str, session: SessionDep, principal=Depends(require_principal)):
    return invoke(session, lambda: service.initialize_profile(session, principal, field_id))


@workspace_router.get("/farms/{farm_id}/runs/{run_id}/evidence")
def evidence(farm_id: str, run_id: str, session: SessionDep, principal=Depends(require_principal)):
    return invoke(session, lambda: service.evidence(session, principal, farm_id, run_id))


@workspace_router.post("/farms/{farm_id}/runs/{run_id}/cancel")
def cancel(farm_id: str, run_id: str, session: SessionDep, principal=Depends(require_principal)):
    return invoke(session, lambda: service.cancel(session, principal, farm_id, run_id))
