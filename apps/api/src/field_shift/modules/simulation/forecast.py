from datetime import UTC, date, datetime, time, timedelta

from field_shift_science.crops.fao56 import crop_water_parameters
from field_shift_science.fields.schemas import Polygon
from field_shift_science.geo_ml.era5_land import field_location
from field_shift_science.geo_ml.ifs import GRID_KM, MODEL_DOCUMENTATION, MODEL_NAME
from field_shift_science.simulation.root_zone_inputs import (
    fetch_ifs_root_zone,
    root_zone_storage_mm,
)
from field_shift_science.simulation.root_zone_model import active_root_zone_correction
from field_shift_science.simulation.schemas import WaterForecast, WaterForecastDay
from field_shift_science.simulation.water_balance import (
    FAO56_BUCKET_VERSION,
    REFERENCE_FIELD_CAPACITY,
    REFERENCE_WILTING_POINT,
    fao56_days,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from field_shift.jobs.models import Job, JobKind, JobStatus
from field_shift.modules.fields.service import require_field
from field_shift.modules.simulation.historical import HistoricalInputError

ROOT_ZONE_SOURCE_UNAVAILABLE = "Root-zone weather forecast is temporarily unavailable."


def water_forecast_run_day(submitted_at: datetime) -> date:
    return submitted_at.astimezone(UTC).date() - timedelta(days=1)


def run_water_forecast(
    field_id: str, crop_name: str, geometry: Polygon, submitted_at: datetime
) -> WaterForecast:
    crop = crop_water_parameters(crop_name)
    if crop is None:
        raise HistoricalInputError("Water forecasts currently support maize, soy and wheat.")

    latitude, longitude = field_location(geometry)
    run = fetch_ifs_root_zone(latitude, longitude, water_forecast_run_day(submitted_at))
    capacity = round(
        1000 * (REFERENCE_FIELD_CAPACITY - REFERENCE_WILTING_POINT) * crop.root_depth_m, 4
    )
    threshold = round((1 - crop.depletion_fraction) * capacity, 4)
    storage = root_zone_storage_mm(run.initial_layers, crop.root_depth_m)
    initial = round(
        min(capacity, max(0.0, storage - 1000 * REFERENCE_WILTING_POINT * crop.root_depth_m)), 4
    )
    physical = fao56_days(
        run.days,
        initial_mm=initial,
        capacity_mm=capacity,
        threshold_mm=threshold,
        coefficient=crop.midseason_coefficient,
    )

    correction = active_root_zone_correction() if crop == crop_water_parameters("maize") else None
    days = []
    model_version = None
    for weather, balance in zip(run.days, physical, strict=True):
        estimate = None
        if (
            correction is not None
            and run.model_cycle == correction.source_cycle
            and weather.soil_temperature_c >= 2
            and correction.supports(latitude, longitude, weather)
        ):
            estimate = round(correction.predict(run, weather, balance.reserve_mm), 4)
            model_version = correction.version
        days.append(
            WaterForecastDay(
                **balance.model_dump(),
                lead_days=weather.lead_days,
                root_zone_estimate_mm=estimate,
            )
        )
    return WaterForecast(
        field_id=field_id,
        season=days[0].date.year,
        crop=crop_name,
        initialized_at=datetime.combine(run.initialized_on, time(), UTC),
        generated_at=datetime.now(UTC),
        method="ml" if model_version is not None else "fao56",
        model_version=model_version,
        physical_model_version=FAO56_BUCKET_VERSION,
        provider=MODEL_NAME,
        source_url=MODEL_DOCUMENTATION,
        grid_resolution_km=GRID_KM,
        grid_latitude=run.grid_latitude,
        grid_longitude=run.grid_longitude,
        root_depth_m=crop.root_depth_m,
        initial_reserve_mm=initial,
        capacity_mm=capacity,
        threshold_mm=threshold,
        field_capacity=REFERENCE_FIELD_CAPACITY,
        wilting_point=REFERENCE_WILTING_POINT,
        crop_coefficient=crop.midseason_coefficient,
        limitations=[
            "The opening reserve uses forecast soil layers from the initialization day.",
            "The bucket uses reference loam, fixed rooting depth and a midseason crop coefficient.",
            "The forecast assumes zero irrigation, snowmelt and runoff and uses grid data.",
        ],
        days=days,
    )


def latest_water_forecast(session: Session, field_id: str) -> WaterForecast | None:
    field = require_field(session, field_id)
    data = session.scalar(
        select(Job.result)
        .where(
            Job.kind == JobKind.SIMULATION,
            Job.field_id == field_id,
            Job.status == JobStatus.SUCCEEDED,
            Job.inputs["analysis"].as_string() == "water_forecast",
            Job.matches_boundary_revision(field.boundary_revision),
            Job.result.is_not(None),
        )
        .order_by(
            Job.result["initialized_at"].as_string().desc(), Job.updated_at.desc(), Job.id.desc()
        )
        .limit(1)
    )
    if data is None:
        return None
    result = WaterForecast.model_validate(data)
    if result.model_version is not None:
        correction = active_root_zone_correction()
        if correction is None or correction.version != result.model_version:
            return result.model_copy(
                update={
                    "method": "fao56",
                    "model_version": None,
                    "days": [
                        day.model_copy(update={"root_zone_estimate_mm": None})
                        for day in result.days
                    ],
                }
            )
    return result
