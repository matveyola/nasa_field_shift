"""Read-only owned-field endpoints; close DB transaction before bounded provider I/O."""

from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from field_shift.db import SessionDep
from field_shift.errors import NotFoundError
from field_shift.modules.access.dependencies import require_principal
from field_shift.modules.fields.models import Field

from . import dashboard, service
from .providers import WeatherUnavailable
from .schemas import RainHistory, WeatherSummary

router = APIRouter(prefix="/fields", tags=["field-weather"])


def owned_field(request, session, field_id):
    principal = require_principal(request)
    field = session.scalar(
        select(Field).where(Field.id == field_id, Field.farm_id == principal.farm_id)
    )
    if field is None:
        raise NotFoundError("Field was not found")
    result = field.id, field.boundary_revision, field.geometry
    session.close()
    return result


@router.get("/{field_id}/weather-dashboard", response_model=dashboard.WeatherDashboard)
def weather_dashboard(
    field_id: str,
    request: Request,
    session: SessionDep,
    start: date,
    end: date,
    geometry_revision: int = Query(ge=0),
):
    field = owned_field(request, session, field_id)
    if geometry_revision != field[1]:
        raise HTTPException(409, "Field boundary changed. Reload the field before requesting data.")
    if not 0 <= (end - start).days < dashboard.MAX_PERIOD_DAYS:
        raise HTTPException(422, "Select a period of 1–92 days.")
    return dashboard.build(*field, start=start, end=end)


@router.get("/{field_id}/weather-summary", response_model=WeatherSummary)
def weather_summary(field_id: str, request: Request, session: SessionDep):
    scope = owned_field(request, session, field_id)
    try:
        return service.summary(*scope)
    except WeatherUnavailable as error:
        raise HTTPException(503, str(error)) from None


@router.get("/{field_id}/rainfall-history", response_model=RainHistory)
def rainfall_history(field_id: str, request: Request, session: SessionDep, typical: bool = False):
    scope = owned_field(request, session, field_id)
    try:
        return service.history(*scope, typical=typical)
    except WeatherUnavailable as error:
        raise HTTPException(503, str(error)) from None
