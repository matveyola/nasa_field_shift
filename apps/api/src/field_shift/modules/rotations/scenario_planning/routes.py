"""Isolated router factory, mounted by #7 behind existing auth/CSRF middleware."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from field_shift_contracts.planning_v1.planning import (
    ApplyPlanRequest,
    FarmPlanningRequest,
    PlanningError,
    ReplanRequest,
)
from pydantic import BeforeValidator

from field_shift.db import SessionDep
from field_shift.modules.access.dependencies import require_principal

from .common import canonical, reason
from .service import PlanningFailure


def json_contract(model):
    """Retain strict JSON date/tuple semantics after FastAPI decodes request JSON."""
    return BeforeValidator(
        lambda value: (
            value if isinstance(value, model) else model.model_validate_json(canonical(value))
        )
    )


PlanningBody = Annotated[FarmPlanningRequest, json_contract(FarmPlanningRequest)]
ApplyBody = Annotated[ApplyPlanRequest, json_contract(ApplyPlanRequest)]
ReplanBody = Annotated[ReplanRequest, json_contract(ReplanRequest)]


class PlanningRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validate(request):
            try:
                chunks = []
                size = 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 8_000_000:
                        raise RequestValidationError([])
                    chunks.append(chunk)
                body = b"".join(chunks)

                async def receive():
                    return {"type": "http.request", "body": body, "more_body": False}

                return await handler(Request(request.scope, receive))
            except RequestValidationError:
                error = PlanningError(
                    request_id=None,
                    code="invalid_input",
                    retryable=False,
                    reasons=[
                        reason(
                            "invalid_input",
                            "Body or parameters violate planning-v1.",
                            "planning-v1",
                            "result",
                        )
                    ],
                )
                return JSONResponse(status_code=422, content=error.model_dump(mode="json"))

        return validate


def create_router(service):
    router = APIRouter(
        prefix="/planning-v1/farms/{farm_id}", tags=["planning-v1"], route_class=PlanningRoute
    )

    def invoke(session, callback):
        try:
            value = callback()
            session.commit()  # Durable before #7's outbox/recovery publisher sees run ID.
            return value
        except PlanningFailure as exc:
            session.rollback()
            codes = {
                "not_found": 404,
                "forbidden": 403,
                "conflict": 409,
                "stale": 409,
                "invalid_input": 422,
                "unavailable": 503,
            }
            error = PlanningError(
                request_id=None,
                code=exc.code,
                retryable=exc.code == "unavailable",
                reasons=[exc.reason],
            )
            return JSONResponse(status_code=codes[exc.code], content=error.model_dump(mode="json"))

    def match(farm_id, payload):
        if farm_id != payload.farm_id:
            raise PlanningFailure("invalid_input", "Path and payload farm differ.")

    @router.post("/runs", status_code=202)
    def submit(
        farm_id: str,
        payload: PlanningBody,
        session: SessionDep,
        principal=Depends(require_principal),
    ):
        def action():
            match(farm_id, payload)
            return service.submit(session, principal, payload)

        return invoke(session, action)

    @router.get("/runs")
    def listing(
        farm_id: str,
        session: SessionDep,
        offset: int = Query(0, ge=0, le=10000),
        limit: int = Query(20, ge=1, le=50),
        principal=Depends(require_principal),
    ):
        return invoke(
            session, lambda: service.list(session, principal, farm_id, offset=offset, limit=limit)
        )

    @router.get("/runs/{run_id}")
    def detail(
        farm_id: str, run_id: str, session: SessionDep, principal=Depends(require_principal)
    ):
        return invoke(session, lambda: service.detail(session, principal, farm_id, run_id))

    @router.post("/runs/{run_id}/apply")
    def apply(
        farm_id: str,
        run_id: str,
        payload: ApplyBody,
        session: SessionDep,
        principal=Depends(require_principal),
    ):
        def action():
            match(farm_id, payload)
            if payload.run_id != run_id:
                raise PlanningFailure("invalid_input", "Path and payload run differ.")
            return service.apply(session, principal, payload)

        return invoke(session, action)

    @router.post("/runs/{run_id}/replan", status_code=202)
    def replan(
        farm_id: str,
        run_id: str,
        payload: ReplanBody,
        session: SessionDep,
        principal=Depends(require_principal),
    ):
        def action():
            match(farm_id, payload.request)
            if payload.previous_run_id != run_id:
                raise PlanningFailure("invalid_input", "Path and previous run differ.")
            return service.replan(session, principal, payload)

        return invoke(session, action)

    return router
