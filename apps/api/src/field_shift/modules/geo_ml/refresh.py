import logging
from argparse import ArgumentParser
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from json import dumps
from os import getenv
from time import sleep

from field_shift_science.crops.fao56 import crop_water_parameters
from field_shift_science.fields.schemas import Polygon
from field_shift_science.geo_ml.era5_land import (
    MoistureSourceError,
    expected_historical_day,
    field_location,
    latest_archive_day,
)
from field_shift_science.geo_ml.moisture_model import MoistureModelUnavailable, get_moisture_model
from field_shift_science.simulation.root_zone_model import active_root_zone_correction
from field_shift_science.weather.scenarios import Scenario
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from field_shift.config import get_settings
from field_shift.db import build_session_factory
from field_shift.jobs import repository
from field_shift.jobs.models import Job, JobKind, JobStatus
from field_shift.jobs.schemas import JobInputs
from field_shift.modules.fields.models import Field
from field_shift.modules.geo_ml.forecast import active_forecast_correction, forecast_run_day
from field_shift.modules.geo_ml.ml_layer import (
    active_moisture_model,
    experimental_enabled,
    latest_supported_day,
)
from field_shift.modules.simulation.forecast import (
    ROOT_ZONE_SOURCE_UNAVAILABLE,
    water_forecast_run_day,
)

logger = logging.getLogger(__name__)
BATCH_SIZE = 100
REPLAY_AFTER = timedelta(hours=2)
REPLAY_DELAYS = (REPLAY_AFTER, timedelta(hours=4), timedelta(hours=8), timedelta(hours=8))
ARCHIVE_RECHECK_AFTER = timedelta(hours=12)


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    field_id: str
    inputs: dict
    kind: JobKind = JobKind.GEO_ML
    transient_failure: str = MoistureSourceError.public_message
    preferred_sample_date: str | None = None
    boundary_revision: int = 0


def enqueue_refresh(
    factory: sessionmaker[Session],
    *,
    now: datetime | None = None,
) -> int:
    at = (now or datetime.now(UTC)).astimezone(UTC)
    historical_day = expected_historical_day(at.date())
    archive_day = latest_archive_day(at.date())
    try:
        model = active_moisture_model(get_moisture_model)
    except MoistureModelUnavailable as error:
        logger.warning("Model unavailable, refreshing ERA5-Land only: %s", error)
        model = None
    ml_day = (
        latest_supported_day(archive_day, model.supports_date)
        if model is not None and (model.promoted or experimental_enabled())
        else None
    )
    raw_inputs = JobInputs(season=historical_day.year, source="historical", latest=True).stored()
    ml_inputs = JobInputs(season=ml_day.year, source="ml").stored() if ml_day else None
    forecast_day = forecast_run_day(at)
    forecast_inputs = (
        JobInputs(season=at.year, source="forecast").stored()
        if getenv("MOISTURE_FORECAST_REFRESH", "1") == "1"
        else None
    )
    forecast_model = active_forecast_correction() if forecast_inputs is not None else None
    forecast_provider = forecast_model.version if forecast_model is not None else "ecmwf-ifs"
    water_inputs = (
        JobInputs(
            season=at.year,
            scenario=Scenario.BASELINE,
            source="forecast",
            analysis="water_forecast",
        ).stored()
        if getenv("WATER_FORECAST_REFRESH", "1") == "1"
        else None
    )
    water_model = active_root_zone_correction() if water_inputs is not None else None
    water_provider = water_model.version if water_model is not None else "fao56-ifs"
    water_day = water_forecast_run_day(at).isoformat()
    after_id = ""
    queued = 0

    while True:
        with factory.begin() as session:
            fields = list(
                session.scalars(
                    select(Field).where(Field.id > after_id).order_by(Field.id).limit(BATCH_SIZE)
                )
            )
            if not fields:
                return queued
            after_id = fields[-1].id
            candidates = {}
            for field in fields:
                raw_key = refresh_key(field, "era5-land:latest-v2", historical_day.isoformat())
                candidates[raw_key] = ScheduledJob(
                    field.id,
                    raw_inputs,
                    preferred_sample_date=historical_day.isoformat(),
                    boundary_revision=field.boundary_revision,
                )
                if forecast_inputs is not None:
                    forecast_key = refresh_key(field, forecast_provider, forecast_day.isoformat())
                    candidates[forecast_key] = ScheduledJob(
                        field.id, forecast_inputs, boundary_revision=field.boundary_revision
                    )
                if water_inputs is not None and crop_water_parameters(field.crop) is not None:
                    water_key = refresh_key(
                        field, f"water:{water_provider}:{field.crop}", water_day
                    )
                    candidates[water_key] = ScheduledJob(
                        field.id,
                        water_inputs,
                        JobKind.SIMULATION,
                        ROOT_ZONE_SOURCE_UNAVAILABLE,
                        boundary_revision=field.boundary_revision,
                    )
                if (
                    model is not None
                    and ml_day is not None
                    and ml_inputs is not None
                    and model.supports_location(
                        *field_location(Polygon.model_validate(field.geometry))
                    )
                ):
                    ml_key = refresh_key(field, model.version, ml_day.isoformat())
                    candidates[ml_key] = ScheduledJob(
                        field.id, ml_inputs, boundary_revision=field.boundary_revision
                    )

            keys_by_candidate = {base: _scheduled_keys(base) for base in candidates}
            scheduled_keys = [key for keys in keys_by_candidate.values() for key in keys]
            existing = {
                key: (status, error, updated_at, result_date)
                for key, status, error, updated_at, result_date in session.execute(
                    select(
                        Job.idempotency_key,
                        Job.status,
                        Job.error,
                        Job.updated_at,
                        Job.result["date"].as_string(),
                    ).where(Job.idempotency_key.in_(scheduled_keys))
                )
            }
            for base, candidate in candidates.items():
                keys = keys_by_candidate[base]
                prior_index = next(
                    (index for index in reversed(range(len(keys))) if keys[index] in existing),
                    -1,
                )
                if prior_index == len(REPLAY_DELAYS):
                    continue
                if prior_index >= 0:
                    status, error, ended_at, result_date = existing[keys[prior_index]]
                    if ended_at.tzinfo is None:
                        ended_at = ended_at.replace(tzinfo=UTC)
                    transient_failure = status == JobStatus.FAILED and error in (
                        candidate.transient_failure,
                        repository.LEASE_EXPIRED_ERROR,
                    )
                    # When the archive had only yesterday's publication, give it one later check.
                    fallback_succeeded = (
                        prior_index == 0
                        and status == JobStatus.SUCCEEDED
                        and candidate.preferred_sample_date is not None
                        and result_date is not None
                        and result_date < candidate.preferred_sample_date
                    )
                    if not (transient_failure or fallback_succeeded):
                        continue
                    delay = (
                        ARCHIVE_RECHECK_AFTER if fallback_succeeded else REPLAY_DELAYS[prior_index]
                    )
                    if at < ended_at + delay:
                        continue
                repository.submit(
                    session,
                    kind=candidate.kind,
                    field_id=candidate.field_id,
                    inputs=candidate.inputs,
                    boundary_revision=candidate.boundary_revision,
                    idempotency_key=keys[prior_index + 1],
                    now=at,
                )
                queued += 1


def _scheduled_keys(base: str) -> tuple[str, ...]:
    return (
        base,
        base + ":replay",
        *(f"{base}:replay:{index}" for index in range(2, len(REPLAY_DELAYS) + 1)),
    )


def refresh_key(field: Field, provider_version: str, data_date: str) -> str:
    inputs = ["interior-point-v1", provider_version, field.id, data_date, field.geometry]
    if field.boundary_revision:
        inputs.append(field.boundary_revision)
    signature = dumps(inputs, sort_keys=True)
    return repository.INTERNAL_KEY_PREFIX + "moisture:" + sha256(signature.encode()).hexdigest()


def main() -> None:
    parser = ArgumentParser(description="Queue daily soil moisture and water forecasts.")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    interval = int(getenv("MOISTURE_REFRESH_INTERVAL_SECONDS", "3600"))
    if interval <= 0:
        raise ValueError("The moisture refresh interval must be positive.")
    factory = build_session_factory(get_settings().database_url)
    logger.info("Starting scheduled soil moisture and water forecast refresh")
    while True:
        try:
            queued = enqueue_refresh(factory)
            if getenv("VEGETATION_REFRESH", "1") == "1":
                from field_shift.modules.imagery.vegetation import enqueue_vegetation_refresh

                queued += enqueue_vegetation_refresh(factory)
            if queued:
                logger.info("Submitted %s forecast and moisture refresh requests", queued)
        except SQLAlchemyError:
            if args.once:
                raise
            logger.exception("Forecast and moisture refresh failed; retrying")
        if args.once:
            return
        sleep(interval)


if __name__ == "__main__":
    main()
