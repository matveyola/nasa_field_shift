import json
import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from field_shift.jobs import repository
from field_shift.jobs.models import JobKind
from field_shift.jobs.repository import Lease
from field_shift.jobs.schemas import JobInputs
from field_shift.modules.fields.history import boundary_for_season
from field_shift.modules.fields.models import Field
from field_shift.modules.fields.service import require_field

logger = logging.getLogger(__name__)
Handler = Callable[[sessionmaker[Session], Lease], dict[str, Any]]


class InvalidModelInput(ValueError):
    pass


class ModelDataUnavailable(RuntimeError):
    pass


def _require_field_snapshot(session: Session, lease: Lease) -> Field:
    field = require_field(session, lease.field_id)
    if field.boundary_revision != lease.boundary_revision:
        raise InvalidModelInput("The field boundary changed. Run this job again.")
    return field


def simulation_handler(factory: sessionmaker[Session], lease: Lease) -> dict[str, Any]:
    if lease.inputs.get("analysis") == "rotation_handoff":
        from field_shift.modules.rotations.handoff.worker import execute

        return execute(factory, lease)

    from field_shift_science.fields.schemas import Polygon
    from field_shift_science.simulation.schemas import WaterSource
    from field_shift_science.simulation.water_balance import WaterInputs
    from field_shift_science.weather.historical import WeatherUnavailableError

    from field_shift.modules.rotations.historical import compare_historical_plans
    from field_shift.modules.rotations.service import get_plan_snapshot
    from field_shift.modules.simulation.forecast import (
        ROOT_ZONE_SOURCE_UNAVAILABLE,
        run_water_forecast,
    )
    from field_shift.modules.simulation.historical import HistoricalInputError
    from field_shift.modules.simulation.service import calculate_water_balance

    inputs = JobInputs.from_stored(lease.inputs)
    if inputs.analysis == "water_forecast":
        with factory() as session:
            field = _require_field_snapshot(session, lease)
            crop = field.crop
            geometry = Polygon.model_validate(field.geometry)
        try:
            return run_water_forecast(
                lease.field_id, crop, geometry, lease.submitted_at
            ).model_dump(mode="json")
        except HistoricalInputError as exc:
            raise InvalidModelInput(str(exc)) from None
        except WeatherUnavailableError:
            raise ModelDataUnavailable(ROOT_ZONE_SOURCE_UNAVAILABLE) from None

    with factory() as session:
        field = _require_field_snapshot(session, lease)
        historical_geometry, _, _ = boundary_for_season(session, field, inputs.season)
        snapshot = WaterInputs(
            field.id,
            field.crop,
            inputs.season,
            inputs.scenario,
            inputs.irrigation_mm,
            source=WaterSource(inputs.source),
            start_date=inputs.start_date,
            end_date=inputs.end_date,
            geometry=historical_geometry,
            initial_reserve_mm=inputs.initial_reserve_mm,
            flowering_start=inputs.flowering_start,
            flowering_end=inputs.flowering_end,
        )
        rotation_plans = None
        if inputs.analysis == "rotation_comparison":
            rotation_plans, _ = get_plan_snapshot(session, field, inputs.season)
    try:
        if rotation_plans is not None:
            return compare_historical_plans(snapshot, rotation_plans).model_dump(mode="json")
        return calculate_water_balance(snapshot).model_dump(mode="json")
    except HistoricalInputError as exc:
        raise InvalidModelInput(str(exc)) from None
    except WeatherUnavailableError:
        raise ModelDataUnavailable("Historical weather is temporarily unavailable.") from None


def geo_handler(factory: sessionmaker[Session], lease: Lease) -> dict[str, Any]:
    from field_shift_science.fields.schemas import Polygon
    from field_shift_science.geo_ml.era5_land import MoistureSourceError

    from field_shift.modules.geo_ml.forecast import run_moisture_forecast
    from field_shift.modules.geo_ml.service import calculate_geo_ml
    from field_shift.modules.imagery.vegetation import run_vegetation_month, vegetation_footprint
    from field_shift.modules.imagery.vegetation_catalog import VegetationUnavailableError
    from field_shift.modules.terrain.service import TerrainUnavailableError, run_terrain_analysis

    inputs = JobInputs.from_stored(lease.inputs)
    with factory() as session:
        field = _require_field_snapshot(session, lease)
        stored_geometry = field.geometry
        if inputs.analysis != "terrain" and inputs.source != "forecast":
            stored_geometry, _, _ = boundary_for_season(session, field, inputs.season)
        geometry = Polygon.model_validate(stored_geometry)
        if inputs.analysis == "vegetation":
            geometry, fingerprint = vegetation_footprint(session, field, inputs.season)
            if inputs.vegetation_mask_hash != fingerprint:
                raise InvalidModelInput("The planted area changed. Run vegetation analysis again.")
    try:
        if inputs.analysis == "vegetation":
            return run_vegetation_month(
                lease.field_id,
                inputs.season,
                inputs.sample_date,
                geometry,
                fingerprint,
                lease.submitted_at,
                lease.boundary_revision,
            ).model_dump(mode="json")
        if inputs.analysis == "terrain":
            return run_terrain_analysis(
                lease.field_id,
                geometry,
                lease.submitted_at,
                lease.boundary_revision,
            ).model_dump(mode="json")
        if inputs.source == "forecast":
            return run_moisture_forecast(lease.field_id, geometry, lease.submitted_at).model_dump(
                mode="json"
            )
        return calculate_geo_ml(
            lease.field_id,
            inputs.season,
            geometry,
            source=inputs.source,
            sample_date=inputs.sample_date,
            submitted_at=lease.submitted_at,
            latest=inputs.latest,
        ).model_dump(mode="json")
    except MoistureSourceError:
        raise ModelDataUnavailable(MoistureSourceError.public_message) from None
    except (VegetationUnavailableError, TerrainUnavailableError) as error:
        raise ModelDataUnavailable(str(error)) from None
    except ValueError as exc:
        raise InvalidModelInput(str(exc)) from None


HANDLERS: Mapping[JobKind, Handler] = {
    JobKind.SIMULATION: simulation_handler,
    JobKind.GEO_ML: geo_handler,
}


def process_job(
    factory: sessionmaker[Session],
    job_id: str,
    *,
    lease_seconds: int,
    handlers: Mapping[JobKind, Handler] = HANDLERS,
    now: datetime | None = None,
) -> None:
    with factory.begin() as session:
        lease = repository.claim(session, job_id, lease_seconds=lease_seconds, now=now)
    if lease is None:
        return

    try:
        result = handlers[lease.kind](factory, lease)
        # Catch invalid adapter output here so the next retry is recorded consistently.
        if not isinstance(result, dict):
            raise TypeError("Job providers must return a JSON object.")
        json.dumps(result, allow_nan=False)
    except InvalidModelInput as error:
        with factory.begin() as session:
            recorded = repository.fail(session, lease, str(error)[:255], now=now, retryable=False)
    except ModelDataUnavailable as error:
        with factory.begin() as session:
            recorded = repository.fail(session, lease, str(error)[:255], now=now)
    except Exception:
        logger.exception("Provider failed for job %s", lease.job_id)
        with factory.begin() as session:
            recorded = repository.fail(session, lease, "Processing failed.", now=now)
    else:
        with factory.begin() as session:
            if lease.inputs.get("analysis") == "rotation_handoff":
                from field_shift.modules.rotations.handoff.worker import succeed

                recorded = succeed(session, lease, result, now=now)
            else:
                recorded = repository.succeed(session, lease, result, now=now)

    if not recorded:
        logger.warning("Discarded completion from expired lease for job %s", lease.job_id)
