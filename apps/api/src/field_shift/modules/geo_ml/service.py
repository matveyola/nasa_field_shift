from datetime import UTC, date, datetime
from hashlib import sha256
from random import Random
from typing import Protocol

from field_shift_science.fields.geometry import contains_point
from field_shift_science.fields.schemas import Polygon
from field_shift_science.geo_ml.era5_land import ERA5LandMoistureModel
from field_shift_science.geo_ml.schemas import GeoResult, MoistureCell, SurfaceSource
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from field_shift.jobs.models import Job, JobKind, JobStatus
from field_shift.modules.fields.history import boundary_for_season
from field_shift.modules.fields.service import require_field


class GeoModel(Protocol):
    def run(self, field_id: str, season: int, geometry: Polygon) -> GeoResult: ...


class DemoGeoModel:
    """Generate a repeatable visual fixture with no live geospatial dependencies."""

    def run(self, field_id: str, season: int, geometry: Polygon) -> GeoResult:
        ring = geometry.coordinates[0]
        x, y = zip(*ring, strict=True)
        resolution = 12
        dx, dy = (max(x) - min(x)) / resolution, (max(y) - min(y)) / resolution
        left, bottom = min(x), min(y)
        seed = sha256(f"{field_id}:{season}".encode()).digest()
        random = Random(seed)
        cells: list[MoistureCell] = []
        for column in range(resolution):
            for row in range(resolution):
                x0, y0 = left + column * dx, bottom + row * dy
                x1, y1 = x0 + dx, y0 + dy
                corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
                if not all(contains_point(ring, *corner) for corner in corners):
                    continue
                value = round(
                    0.12 + 0.14 * (column + row) / (resolution * 2) + random.random() * 0.06, 3
                )
                cells.append(
                    MoistureCell(
                        geometry=Polygon(type="Polygon", coordinates=[[*corners, corners[0]]]),
                        value=value,
                    )
                )
        if not cells:
            cells.append(MoistureCell(geometry=geometry, value=0.22))
        return GeoResult(
            field_id=field_id,
            season=season,
            demo=True,
            date=date(season, 8, 1),
            depth_cm=(0, 5),
            unit="m³/m³",
            minimum=0.12,
            maximum=0.32,
            cells=cells,
        )


DEFAULT_GEO_MODEL: GeoModel = DemoGeoModel()
HISTORICAL_GEO_MODEL = ERA5LandMoistureModel()


def calculate_geo_ml(
    field_id: str,
    season: int,
    geometry: Polygon,
    model: GeoModel | None = None,
    *,
    source: SurfaceSource = "demo",
    sample_date: date | None = None,
    submitted_at: datetime | None = None,
    latest: bool = False,
) -> GeoResult:
    if source == "ml":
        from field_shift.modules.geo_ml.ml_layer import run_ml_layer

        return run_ml_layer(
            field_id, season, geometry, sample_date, submitted_at or datetime.now(UTC)
        )
    if source == "historical":
        if latest:
            today = (submitted_at or datetime.now(UTC)).astimezone(UTC).date()
            return HISTORICAL_GEO_MODEL.run_latest(field_id, season, geometry, today)
        day = sample_date or date(season, 8, 1)
        if day.year != season:
            raise ValueError("The archive date must be within the selected season")
        return HISTORICAL_GEO_MODEL.run(field_id, season, geometry, day)
    return (model if model is not None else DEFAULT_GEO_MODEL).run(field_id, season, geometry)


def run_geo_ml(
    session: Session, *, field_id: str, season: int, model: GeoModel | None = None
) -> GeoResult:
    field = require_field(session, field_id)
    stored_geometry, _, _ = boundary_for_season(session, field, season)
    geometry = Polygon.model_validate(stored_geometry)
    return calculate_geo_ml(field_id, season, geometry, model)


def latest_surface_moisture(
    session: Session,
    field_id: str,
    season: int | None,
    source: SurfaceSource | None = None,
    sample_date: date | None = None,
) -> GeoResult | None:
    field = require_field(session, field_id)
    if season is None:
        season = sample_date.year if sample_date else (2026 if source in (None, "demo") else None)
    if source != "demo":
        statement = select(Job.result).where(
            Job.kind == JobKind.GEO_ML,
            Job.field_id == field_id,
            Job.status == JobStatus.SUCCEEDED,
            Job.inputs["source"].as_string() == (source or "historical"),
            or_(
                Job.inputs["analysis"].as_string().is_(None),
                Job.inputs["analysis"].as_string() == "water_balance",
            ),
            Job.matches_boundary_revision(field.boundary_revision),
            Job.result.is_not(None),
        )
        if season is not None:
            statement = statement.where(Job.result["season"].as_integer() == season)
        if sample_date is not None:
            statement = statement.where(Job.result["date"].as_string() == sample_date.isoformat())
        if source == "ml":
            from field_shift.modules.geo_ml.ml_layer import experimental_enabled

            if not experimental_enabled():
                statement = statement.where(
                    Job.result["source"]["experimental"].as_boolean().is_not(True)
                )
        previous = session.scalar(
            statement.order_by(
                Job.result["date"].as_string().desc(), Job.updated_at.desc(), Job.id.desc()
            ).limit(1)
        )
        if previous is not None:
            return GeoResult.model_validate(previous)
    if (source is None or source == "demo") and season is not None and sample_date is None:
        geometry, _, _ = boundary_for_season(session, field, season)
        return calculate_geo_ml(field_id, season, Polygon.model_validate(geometry))
    return None
