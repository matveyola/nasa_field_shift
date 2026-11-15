import logging
from os import getenv

from field_shift_science.fields.schemas import Polygon
from field_shift_science.geo_ml.era5_land import field_location
from field_shift_science.rotations.yield_model import (
    SOURCE_URL,
    RotationYieldModelUnavailable,
    get_rotation_yield_model,
)
from sqlalchemy.orm import Session

from field_shift.modules.fields.history import boundary_for_season
from field_shift.modules.fields.service import require_field
from field_shift.modules.onboarding.profiles import completed_setup_inputs
from field_shift.modules.rotations.schemas import (
    RotationYieldPlan,
    RotationYieldRead,
    RotationYieldYear,
)
from field_shift.modules.rotations.service import crop_for_season, get_plan_snapshot

logger = logging.getLogger(__name__)


def load_rotation_yield_model():
    try:
        return get_rotation_yield_model()
    except RotationYieldModelUnavailable:
        logger.exception("Rotation yield artifact failed validation")
        return None


def get_yield_comparison(session: Session, field_id: str, season: int) -> RotationYieldRead:
    field = require_field(session, field_id)
    schedules, _ = get_plan_snapshot(session, field, season)
    geometry, _, _ = boundary_for_season(session, field, season)
    latitude, longitude = field_location(Polygon.model_validate(geometry))
    model = load_rotation_yield_model()
    enabled = model is not None and (
        model.promoted or getenv("ROTATION_YIELD_ENABLE_EXPERIMENTAL", "0") == "1"
    )
    setup = completed_setup_inputs(session, (field_id,)).get(field_id)
    first_preceding_crop = crop_for_season(field, season, setup)
    plans = []
    for plan in schedules:
        estimates = []
        sequence = tuple(item.crop for item in plan.crops)
        preceding_crop = first_preceding_crop
        for item in plan.crops:
            estimate = (
                model.estimate(
                    latitude=latitude,
                    longitude=longitude,
                    crop=item.crop,
                    preceding_crop=preceding_crop,
                    crop_sequence=sequence,
                )
                if model is not None and enabled and preceding_crop is not None
                else None
            )
            reason = None
            if estimate is None:
                if model is None:
                    reason = "A yield model is unavailable."
                elif not enabled:
                    reason = "The model is available for research preview."
                elif preceding_crop is None:
                    reason = "The preceding crop is unknown for this season."
                elif item.crop.strip().casefold() not in ("maize", "corn"):
                    reason = "The trial comparison covers maize yields."
                else:
                    reason = "This rotation or location is outside the supported trial scope."
            estimates.append(
                RotationYieldYear(
                    year=item.year,
                    crop=item.crop,
                    previous_crop=preceding_crop,
                    change_t_ha=estimate.delta_t_ha if estimate is not None else None,
                    lower_t_ha=estimate.lower_t_ha if estimate is not None else None,
                    upper_t_ha=estimate.upper_t_ha if estimate is not None else None,
                    reason=reason,
                )
            )
            preceding_crop = item.crop
        plans.append(RotationYieldPlan(id=plan.id, estimates=estimates))
    return RotationYieldRead(
        field_id=field_id,
        season=season,
        model_version=model.version if model is not None else None,
        status=(
            "validated"
            if model is not None and model.promoted
            else "experimental"
            if enabled
            else "unavailable"
        ),
        source_url=SOURCE_URL if model is not None else None,
        training_sites=model.validation.get("training_sites") if model is not None else None,
        plans=plans,
    )
