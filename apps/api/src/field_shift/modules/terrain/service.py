from datetime import UTC, datetime
from hashlib import sha256
from math import ceil, sqrt

import numpy as np
from rasterio.features import geometry_mask
from rasterio.warp import transform, transform_geom
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from field_shift.jobs import repository
from field_shift.jobs.models import Job, JobKind, JobStatus
from field_shift.jobs.schemas import JobInputs
from field_shift.modules.fields.geometry import contains_point
from field_shift.modules.fields.models import Field
from field_shift.modules.fields.schemas import Polygon
from field_shift.modules.fields.service import require_field
from field_shift.modules.terrain.dem import (
    GRID_METERS,
    TerrainCoverageError,
    TerrainGrid,
    TerrainUnavailableError,
    load_terrain_grid,
)
from field_shift.modules.terrain.schemas import TerrainResult, TerrainVector
from field_shift.modules.weather.scenarios import Scenario

__all__ = ["TerrainUnavailableError", "latest_terrain", "run_terrain_analysis", "submit_terrain"]
MODEL_VERSION = "cop-dem-glo30-gradient-v1"
MAX_VECTORS = 200
MIN_SLOPE_PERCENT = 1.0
ATTRIBUTION = (
    "produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and "
    "© Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS "
    "by the European Union and ESA; all rights reserved"
)
LIMITATIONS = [
    "Arrows show potential downhill directions from the surface gradient, not measured water flow.",
    "The approximately 30 m surface model includes vegetation and buildings; field drains and "
    "small earthworks are unresolved.",
    "No rainfall, infiltration, flow accumulation, flood depth or runoff volume is simulated.",
    "Slopes below 1% are omitted; remaining directions are still uncertain "
    "and need field checking.",
    "The elevation source is static and does not describe current field changes.",
]


def _vectors(grid: TerrainGrid, inside: np.ndarray) -> list[TerrainVector]:
    z = grid.elevation.astype("float64", copy=False)
    if min(z.shape) < 3:
        return []
    # Horn's 3x3 surface derivative on a 30 m projected grid. A complete interior
    # neighborhood is required so nodata and vegetation outside the field cannot create arrows.
    neighborhoods = np.lib.stride_tricks.sliding_window_view(z, (3, 3))
    masks = np.lib.stride_tricks.sliding_window_view(inside, (3, 3))
    valid = np.isfinite(neighborhoods).all(axis=(-1, -2)) & masks.all(axis=(-1, -2))
    dz_east = (
        neighborhoods[:, :, 0, 2]
        + 2 * neighborhoods[:, :, 1, 2]
        + neighborhoods[:, :, 2, 2]
        - neighborhoods[:, :, 0, 0]
        - 2 * neighborhoods[:, :, 1, 0]
        - neighborhoods[:, :, 2, 0]
    ) / (8 * GRID_METERS)
    dz_south = (
        neighborhoods[:, :, 2, 0]
        + 2 * neighborhoods[:, :, 2, 1]
        + neighborhoods[:, :, 2, 2]
        - neighborhoods[:, :, 0, 0]
        - 2 * neighborhoods[:, :, 0, 1]
        - neighborhoods[:, :, 0, 2]
    ) / (8 * GRID_METERS)
    magnitude = np.hypot(dz_east, dz_south)
    valid &= magnitude * 100 >= MIN_SLOPE_PERCENT
    # Evenly spaced arrows, at least 60 m apart; fixed bounded response size.
    stride = max(2, ceil(sqrt(int(inside.sum()) / MAX_VECTORS)))
    rows, columns = np.nonzero(valid)
    chosen = (rows % stride == 0) & (columns % stride == 0)
    rows, columns = rows[chosen], columns[chosen]
    if len(rows) > MAX_VECTORS:
        keep = np.linspace(0, len(rows) - 1, MAX_VECTORS, dtype=int)
        rows, columns = rows[keep], columns[keep]
    if not len(rows):
        return []
    x, y = grid.transform * (columns + 1.5, rows + 1.5)
    length = GRID_METERS * 0.75
    end_x = x - dz_east[rows, columns] / magnitude[rows, columns] * length
    end_y = y + dz_south[rows, columns] / magnitude[rows, columns] * length
    lon, lat = transform(grid.epsg, "EPSG:4326", x.tolist(), y.tolist())
    end_lon, end_lat = transform(grid.epsg, "EPSG:4326", end_x.tolist(), end_y.tolist())
    return [
        TerrainVector(
            start=(round(lon[i], 8), round(lat[i], 8)),
            end=(round(end_lon[i], 8), round(end_lat[i], 8)),
            slope_percent=round(float(magnitude[row, column] * 100), 2),
            elevation_m=round(float(z[row + 1, column + 1]), 2),
        )
        for i, (row, column) in enumerate(zip(rows, columns, strict=True))
    ]


def run_terrain_analysis(
    field_id: str, geometry: Polygon, submitted_at: datetime, boundary_revision: int = 0
) -> TerrainResult:
    result = TerrainResult(
        field_id=field_id,
        boundary_revision=boundary_revision,
        generated_at=datetime.now(UTC),
        status="unavailable",
        model_version=MODEL_VERSION,
        coverage_percent=0,
        minimum_display_slope_percent=MIN_SLOPE_PERCENT,
        limitations=LIMITATIONS,
        attribution=ATTRIBUTION,
    )
    try:
        grid = load_terrain_grid(geometry)
    except TerrainCoverageError as error:
        return result.model_copy(update={"reason": str(error)})
    inside = geometry_mask(
        [transform_geom("EPSG:4326", grid.epsg, geometry.model_dump())],
        out_shape=grid.elevation.shape,
        transform=grid.transform,
        invert=True,
    )
    total = int(inside.sum())
    if total == 0:
        return result.model_copy(
            update={
                "reason": "The field is too small for the 30 m elevation grid.",
                "source_tiles": list(grid.source_tiles),
            }
        )
    valid = inside & np.isfinite(grid.elevation)
    coverage = round(float(valid.sum()) / total * 100, 1)
    if coverage < 90:
        return result.model_copy(
            update={
                "coverage_percent": coverage,
                "source_tiles": list(grid.source_tiles),
                "reason": "Less than 90% of the field has usable elevation data.",
            }
        )
    ring = geometry.coordinates[0]
    vectors = [
        vector
        for vector in _vectors(grid, inside)
        if contains_point(ring, *vector.start) and contains_point(ring, *vector.end)
    ]
    return result.model_copy(
        update={
            "status": "ready",
            "coverage_percent": coverage,
            "vectors": vectors,
            "source_tiles": list(grid.source_tiles),
            "elevation_min_m": round(float(grid.elevation[valid].min()), 2),
            "elevation_max_m": round(float(grid.elevation[valid].max()), 2),
            "reason": None
            if vectors
            else ("No sufficiently resolved downhill directions in this field."),
        }
    )


def latest_terrain(session: Session, field_id: str) -> TerrainResult | None:
    field = require_field(session, field_id)
    data = session.scalar(
        select(Job.result)
        .where(
            Job.kind == JobKind.GEO_ML,
            Job.field_id == field_id,
            Job.status == JobStatus.SUCCEEDED,
            Job.inputs["analysis"].as_string() == "terrain",
            Job.matches_boundary_revision(field.boundary_revision),
            Job.result["model_version"].as_string() == MODEL_VERSION,
            Job.result.is_not(None),
        )
        .order_by(Job.updated_at.desc(), Job.id.desc())
        .limit(1)
    )
    return TerrainResult.model_validate(data) if data is not None else None


def submit_terrain(session: Session, field_id: str) -> Job:
    """Idempotent manual analysis: reuse static results and resume durable pending work."""
    field = require_field(session, field_id)
    # Serialize submissions and boundary edits for this field; the job key unique
    # constraint remains the final arbiter even on databases without row locking.
    session.execute(select(Field.id).where(Field.id == field.id).with_for_update())
    session.refresh(field)
    identity = sha256(f"{field.id}:{field.boundary_revision}:{MODEL_VERSION}".encode()).hexdigest()
    prefix = repository.INTERNAL_KEY_PREFIX + "terrain:" + identity + ":"
    matching = (
        Job.kind == JobKind.GEO_ML,
        Job.field_id == field.id,
        Job.idempotency_key.startswith(prefix),
    )
    reusable = session.scalar(
        select(Job)
        .where(*matching, Job.status != JobStatus.FAILED)
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )
    if reusable is not None:
        return reusable
    ordinal = session.scalar(select(func.count(Job.id)).where(*matching)) or 0
    return repository.submit(
        session,
        kind=JobKind.GEO_ML,
        field_id=field.id,
        inputs=JobInputs(
            season=field.season,
            scenario=Scenario.BASELINE,
            source="historical",
            analysis="terrain",
        ).stored(),
        boundary_revision=field.boundary_revision,
        idempotency_key=prefix + str(ordinal),
    )
