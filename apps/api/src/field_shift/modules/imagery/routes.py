from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from field_shift_science.fields.schemas import Polygon

from field_shift.db import SessionDep
from field_shift.modules.fields.service import require_field
from field_shift.modules.imagery.sentinel import (
    EARLIEST_SCENE,
    CatalogUnavailableError,
    SatelliteImage,
    SatelliteScene,
    satellite_image_at,
    satellite_scene,
)
from field_shift.modules.imagery.vegetation_schemas import (
    VegetationRefresh,
    VegetationSceneResult,
    VegetationSeason,
)

router = APIRouter(prefix="/fields", tags=["satellite imagery"])


@router.get("/{field_id}/vegetation", response_model=VegetationSeason)
def field_vegetation(
    field_id: str,
    season: int,
    session: SessionDep,
    include_cells: bool = True,
) -> VegetationSeason:
    from field_shift.modules.imagery.vegetation import vegetation_season

    try:
        return vegetation_season(session, field_id, season, include_cells=include_cells)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@router.get("/{field_id}/vegetation/scene", response_model=VegetationSceneResult | None)
def field_vegetation_scene(
    field_id: str,
    season: int,
    observation_date: date,
    scene_id: Annotated[str, Query(min_length=1, max_length=200)],
    session: SessionDep,
) -> VegetationSceneResult | None:
    from field_shift.modules.imagery.vegetation import vegetation_scene

    try:
        return vegetation_scene(session, field_id, season, observation_date, scene_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@router.post("/{field_id}/vegetation/refresh", response_model=VegetationRefresh, status_code=202)
def refresh_vegetation(field_id: str, season: int, session: SessionDep) -> VegetationRefresh:
    from field_shift.modules.imagery.vegetation import queue_vegetation_refresh

    try:
        jobs = queue_vegetation_refresh(session, field_id, season, retry_failed=True)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    session.commit()
    return VegetationRefresh(jobs=jobs)


@router.get("/satellite", response_model=SatelliteImage | None)
def point_satellite(
    longitude: Annotated[float, Query(ge=-180, le=180)],
    latitude: Annotated[float, Query(ge=-80, le=84)],
) -> SatelliteImage | None:
    try:
        return satellite_image_at(longitude, latitude, datetime.now(UTC).date())
    except CatalogUnavailableError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None


@router.get("/{field_id}/satellite", response_model=SatelliteScene | None)
def field_satellite(
    field_id: str,
    session: SessionDep,
    on_or_before: date | None = None,
) -> SatelliteScene | None:
    today = datetime.now(UTC).date()
    cutoff = on_or_before if on_or_before is not None else today
    if not EARLIEST_SCENE <= cutoff <= today:
        raise HTTPException(
            status_code=422, detail="Choose a satellite date between June 23, 2015 and today."
        )
    with session.begin():
        field = require_field(session, field_id)
        geometry = Polygon.model_validate(field.geometry)
    try:
        return satellite_scene(field_id, geometry, cutoff)
    except CatalogUnavailableError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None
