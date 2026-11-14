from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from field_shift.modules.fields.models import Field
from field_shift.modules.fields.service import require_field
from field_shift.modules.rotations.models import RotationPreference
from field_shift.modules.rotations.schemas import (
    MainPlanUpdate,
    PlanId,
    PlanUpdate,
    RotationCrop,
    RotationPlan,
    RotationPlanSchedule,
    RotationRead,
)
from field_shift.modules.simulation.service import calculate_water_balance
from field_shift.modules.simulation.water_balance import WaterInputs, stress_summary
from field_shift.modules.weather.scenarios import Scenario


def get_plan_snapshot(
    session: Session, field: Field, season: int
) -> tuple[list[RotationPlanSchedule], PlanId]:
    preference = session.get(RotationPreference, (field.id, season))
    fallbacks = [
        crop for crop in ("Maize", "Soy", "Wheat") if crop.casefold() != field.crop.casefold()
    ]
    first = preference.plan_a_crops if preference and preference.plan_a_crops else None
    second = preference.plan_b_crops if preference and preference.plan_b_crops else None
    crops_a = first or [field.crop, *fallbacks[:2]]
    crops_b = second or [*fallbacks[:2], field.crop]
    ids: tuple[PlanId, PlanId] = ("plan-a", "plan-b")
    schedules = [
        RotationPlanSchedule(
            id=plan_id,
            name=name,
            crops=[
                RotationCrop(year=season + offset, crop=crop)
                for offset, crop in enumerate(crops, 1)
            ],
        )
        for plan_id, name, crops in zip(ids, ("Plan A", "Plan B"), (crops_a, crops_b), strict=True)
    ]
    return schedules, cast(PlanId, preference.main_plan_id) if preference else "plan-a"


def get_rotations(session: Session, field_id: str, season: int, scenario: Scenario) -> RotationRead:
    field = require_field(session, field_id)
    schedules, main_plan_id = get_plan_snapshot(session, field, season)
    plans: list[RotationPlan] = []
    demo = False
    for schedule in schedules:
        first_crop = schedule.crops[0]
        result = calculate_water_balance(
            WaterInputs(field.id, first_crop.crop, first_crop.year, scenario)
        )
        demo |= result.demo
        longest, sensitive = stress_summary(result)
        plans.append(
            RotationPlan(
                id=schedule.id,
                name=schedule.name,
                crops=schedule.crops,
                stress_days=result.stress_days,
                longest_episode=longest,
                sensitive_days=sensitive,
            )
        )
    return RotationRead(
        plans=plans,
        main_plan_id=main_plan_id,
        demo=demo,
    )


def _lock_preferences(session: Session, field_id: str, season: int) -> RotationPreference:
    field = require_field(session, field_id)
    session.execute(select(Field.id).where(Field.id == field.id).with_for_update()).scalar_one()
    preference = session.get(RotationPreference, (field.id, season))
    if preference is None:
        preference = RotationPreference(field_id=field.id, season=season, main_plan_id="plan-a")
        session.add(preference)
    return preference


def set_main(session: Session, payload: MainPlanUpdate) -> MainPlanUpdate:
    preference = _lock_preferences(session, payload.field_id, payload.season)
    preference.main_plan_id = payload.plan_id
    session.commit()
    return payload


def save_plan(session: Session, plan_id: PlanId, payload: PlanUpdate) -> PlanUpdate:
    preference = _lock_preferences(session, payload.field_id, payload.season)
    if plan_id == "plan-a":
        preference.plan_a_crops = list(payload.crops)
    else:
        preference.plan_b_crops = list(payload.crops)
    session.commit()
    return payload
