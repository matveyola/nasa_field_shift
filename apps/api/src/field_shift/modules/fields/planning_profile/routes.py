"""Isolated authenticated router; mount under /api in integration #7."""

import json
from typing import Annotated

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from field_shift_contracts.planning_v1 import (
    FieldProfileSnapshot,
    PlanningError,
    ProfileWriteRequest,
)
from pydantic import BeforeValidator

from field_shift.db import SessionDep
from field_shift.modules.access.dependencies import require_principal

from .service import ProfileError, ProfileRepository


class PlanningRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                return error_response(ProfileError("invalid_input", "Invalid planning payload"))

        return validated_handler


def parse_json_wire(value):
    if isinstance(value, ProfileWriteRequest):
        return value
    return ProfileWriteRequest.model_validate_json(json.dumps(value))


WireProfileWrite = Annotated[ProfileWriteRequest, BeforeValidator(parse_json_wire)]

router = APIRouter(
    prefix="/planning-v1/farms/{farm_id}/fields/{field_id}",
    tags=["planning-profile"],
    route_class=PlanningRoute,
)


def authorize(request, farm_id, write=False):
    principal = require_principal(request)
    if str(principal.farm_id) != farm_id:
        raise ProfileError("not_found", "Field not found")
    if write and principal.role not in {"admin", "editor"}:
        raise ProfileError("forbidden", "Editing requires editor or administrator access")


def error_response(exc, request_id=None):
    from .evidence import reason

    status = {
        "not_found": 404,
        "forbidden": 403,
        "stale": 409,
        "conflict": 409,
        "invalid_input": 422,
    }
    body = PlanningError(
        request_id=request_id,
        code=exc.code,
        retryable=False,
        reasons=[reason(exc.code, exc.message)],
    )
    return JSONResponse(status_code=status[exc.code], content=body.model_dump(mode="json"))


@router.get("/profile", response_model=FieldProfileSnapshot)
def get_profile(farm_id: str, field_id: str, request: Request, session: SessionDep):
    try:
        authorize(request, farm_id)
        return ProfileRepository(session).latest(farm_id, field_id)
    except ProfileError as exc:
        return error_response(exc)


@router.put("/profile", response_model=FieldProfileSnapshot)
def put_profile(
    farm_id: str, field_id: str, payload: WireProfileWrite, request: Request, session: SessionDep
):
    try:
        authorize(request, farm_id, write=True)
        return ProfileRepository(session).write(farm_id, field_id, payload)
    except ProfileError as exc:
        return error_response(exc, payload.request_id)
