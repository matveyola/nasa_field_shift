from datetime import date

from fastapi import APIRouter, status
from field_shift_science.fields.schemas import (
    FieldBoundaryUpdate,
    FieldCreate,
    FieldPlantingUpdate,
    FieldRead,
    Season,
)
from field_shift_science.geo_ml.schemas import GeoResult, MoistureForecast, SurfaceSource

from field_shift.db import SessionDep
from field_shift.modules.fields import service
from field_shift.modules.geo_ml.forecast import latest_moisture_forecast
from field_shift.modules.geo_ml.service import latest_surface_moisture

router = APIRouter(prefix="/fields", tags=["fields"])


@router.get("", response_model=list[FieldRead])
def get_fields(session: SessionDep):
    return service.list_fields(session)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=FieldRead)
def post_field(payload: FieldCreate, session: SessionDep):
    return service.create_field(session, payload)


@router.patch("/{field_id}/boundary", response_model=FieldRead)
def patch_boundary(field_id: str, payload: FieldBoundaryUpdate, session: SessionDep):
    return service.update_boundary(session, field_id, payload)


@router.put("/{field_id}/planting/{season}", response_model=FieldRead)
def put_planting(field_id: str, season: Season, payload: FieldPlantingUpdate, session: SessionDep):
    return service.update_planting(session, field_id, season, payload)


@router.get("/{field_id}/moisture", response_model=GeoResult | None)
def moisture(
    field_id: str,
    session: SessionDep,
    season: Season | None = None,
    source: SurfaceSource | None = None,
    sample_date: date | None = None,
):
    return latest_surface_moisture(session, field_id, season, source, sample_date)


@router.get("/{field_id}/moisture-forecast", response_model=MoistureForecast | None)
def moisture_forecast(field_id: str, session: SessionDep):
    return latest_moisture_forecast(session, field_id)
