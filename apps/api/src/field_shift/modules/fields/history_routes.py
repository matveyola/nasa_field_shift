from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from field_shift_science.fields.schemas import Polygon, Season
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from field_shift.db import SessionDep
from field_shift.modules.fields.history import boundary_for_season, versions
from field_shift.modules.fields.models import PreparedContour
from field_shift.modules.fields.service import require_field

router = APIRouter(prefix="/fields", tags=["field history and contours"])


class BoundaryVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    revision: int
    effective_season: int
    geometry: Polygon
    area_ha: float
    created_at: datetime
    source: str


class PreparedContourRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    geometry: Polygon
    area_ha: float
    year: int
    source: str
    source_url: str
    license: str
    region: str


@router.get("/prepared-contours")
def prepared_contours(
    session: SessionDep,
    west: Annotated[float, Query(ge=-180, le=180)],
    south: Annotated[float, Query(ge=-90, le=90)],
    east: Annotated[float, Query(ge=-180, le=180)],
    north: Annotated[float, Query(ge=-90, le=90)],
):
    if not (west < east <= west + 0.2 and south < north <= south + 0.2):
        raise HTTPException(422, "Choose a map window no larger than 0.2° in each direction.")
    records = list(
        session.scalars(
            select(PreparedContour)
            .where(
                PreparedContour.west <= east,
                PreparedContour.east >= west,
                PreparedContour.south <= north,
                PreparedContour.north >= south,
            )
            .order_by(PreparedContour.id)
            .limit(201)
        )
    )
    return {
        "contours": [PreparedContourRead.model_validate(row) for row in records[:200]],
        "truncated": len(records) > 200,
        "limitation": "Historical remote-sensing estimates. Review before saving.",
    }


@router.get("/prepared-contours/coverage")
def contour_coverage(session: SessionDep):
    rows = session.execute(
        select(
            PreparedContour.region,
            PreparedContour.year,
            PreparedContour.source_url,
            PreparedContour.license,
            func.count().label("count"),
            func.min(PreparedContour.west).label("west"),
            func.max(PreparedContour.east).label("east"),
            func.min(PreparedContour.south).label("south"),
            func.max(PreparedContour.north).label("north"),
        )
        .group_by(
            PreparedContour.region,
            PreparedContour.year,
            PreparedContour.source_url,
            PreparedContour.license,
        )
        .limit(100)
    ).mappings()
    return [dict(row) for row in rows]


@router.get("/{field_id}/boundary-history", response_model=list[BoundaryVersionRead])
def boundary_history(field_id: str, session: SessionDep):
    require_field(session, field_id)
    return versions(session, field_id)


@router.get("/{field_id}/boundary-history/{season}")
def seasonal_boundary(field_id: str, season: Season, session: SessionDep):
    field = require_field(session, field_id)
    geometry, area, extrapolated = boundary_for_season(session, field, season)
    return {
        "field_id": field.id,
        "season": season,
        "geometry": geometry,
        "area_ha": area,
        "boundary_revision": field.boundary_revision,
        "earliest_boundary_reused": extrapolated,
    }
