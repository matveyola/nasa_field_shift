import logging
from datetime import UTC, date, datetime, time, timedelta

from field_shift_science.fields.schemas import Polygon
from field_shift_science.geo_ml.era5_land import field_location
from field_shift_science.geo_ml.forecast_model import ForecastCorrection, get_forecast_correction
from field_shift_science.geo_ml.ifs import (
    CURRENT_MODEL_CYCLE,
    GRID_KM,
    MODEL_DOCUMENTATION,
    MODEL_NAME,
    fetch_ifs_run,
)
from field_shift_science.geo_ml.schemas import (
    MoistureForecast,
    MoistureForecastDay,
    MoistureInterval,
)
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from field_shift.jobs.models import Job, JobKind, JobStatus
from field_shift.modules.fields.service import require_field

logger = logging.getLogger(__name__)


def forecast_run_day(submitted_at: datetime) -> date:
    return submitted_at.astimezone(UTC).date() - timedelta(days=1)


def active_forecast_correction() -> ForecastCorrection | None:
    try:
        model = get_forecast_correction()
    except (OSError, ValidationError):
        logger.exception("Forecast correction unavailable. Serving the ECMWF forecast.")
        return None
    if model is not None and model.promoted and model.source_cycle == CURRENT_MODEL_CYCLE:
        return model
    return None


def run_moisture_forecast(
    field_id: str, geometry: Polygon, submitted_at: datetime
) -> MoistureForecast:
    initialized_on = forecast_run_day(submitted_at)
    latitude, longitude = field_location(geometry)
    run = fetch_ifs_run(latitude, longitude, initialized_on)
    correction = active_forecast_correction()
    days = []
    model_version = None
    for day in run.days:
        value = day.surface_moisture
        interval = None
        if (
            correction is not None
            and correction.source_cycle == run.model_cycle
            and correction.supports(latitude, longitude, day.date)
            and day.soil_temperature_c >= 2
        ):
            value = correction.predict(run, day)
            width = correction.interval_half_width[day.lead_days - 1]
            lead_validation = correction.validation["leads"][str(day.lead_days)]
            interval = MoistureInterval(
                lower=round(max(0, value - width), 3),
                upper=round(min(1, value + width), 3),
                coverage=0.9,
                empirical_coverage=lead_validation["interval_coverage"],
            )
            model_version = correction.version
        days.append(
            MoistureForecastDay(
                date=day.date,
                lead_days=day.lead_days,
                value=round(value, 3),
                ifs_value=day.surface_moisture,
                precipitation_mm=day.precipitation_mm,
                et0_mm=day.et0_mm,
                interval=interval,
            )
        )
    return MoistureForecast(
        field_id=field_id,
        initialized_at=datetime.combine(run.initialized_on, time(), UTC),
        fetched_at=datetime.now(UTC),
        model_version=model_version,
        provider=MODEL_NAME,
        source_url=MODEL_DOCUMENTATION,
        resolution_km=GRID_KM,
        grid_latitude=run.grid_latitude,
        grid_longitude=run.grid_longitude,
        days=days,
    )


def latest_moisture_forecast(session: Session, field_id: str) -> MoistureForecast | None:
    field = require_field(session, field_id)
    previous = session.scalar(
        select(Job.result)
        .where(
            Job.kind == JobKind.GEO_ML,
            Job.field_id == field_id,
            Job.status == JobStatus.SUCCEEDED,
            Job.inputs["source"].as_string() == "forecast",
            Job.matches_boundary_revision(field.boundary_revision),
            Job.result.is_not(None),
        )
        .order_by(Job.result["initialized_at"].as_string().desc(), Job.updated_at.desc())
        .limit(1)
    )
    if previous is None:
        return None
    result = MoistureForecast.model_validate(previous)
    if result.model_version is not None:
        active = active_forecast_correction()
        if active is None or active.version != result.model_version:
            days = [
                day.model_copy(update={"value": day.ifs_value, "interval": None})
                for day in result.days
            ]
            return result.model_copy(update={"model_version": None, "days": days})
    return result
