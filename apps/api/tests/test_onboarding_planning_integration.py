from uuid import uuid4

import pytest

from field_shift.db import build_session_factory
from field_shift.errors import ConflictError
from field_shift.models import metadata
from field_shift.modules.fields.models import Field
from field_shift.modules.onboarding.schemas import SetupInputs
from field_shift.modules.onboarding.service import complete_setup, save_setup
from field_shift.modules.rotations.planning_schemas import PlanningRequest
from field_shift.modules.rotations.planning_service import (
    apply_draft,
    create_draft,
    planning_context,
)
from field_shift.modules.rotations.schemas import MainPlanUpdate
from field_shift.modules.rotations.service import get_plan_snapshot, set_main
from field_shift.seed import seed_demo


@pytest.fixture
def session():
    factory = build_session_factory("sqlite://")
    engine = factory.kw["bind"]
    metadata.create_all(engine)
    try:
        with factory() as db:
            seed_demo(db)
            yield db
    finally:
        engine.dispose()


def onboard(session, *, allowed_crops, priority, history):
    setup_id = uuid4()
    save_setup(
        session,
        setup_id,
        SetupInputs.model_validate(
            {
                "country_code": "UA",
                "name": "Orchard Road",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[30.4, 50.2], [30.401, 50.2], [30.401, 50.201], [30.4, 50.2]]],
                },
                "history": history,
                "allowed_crops": allowed_crops,
                "priority": priority,
                "irrigation": True,
            }
        ),
    )
    return complete_setup(session, setup_id).field.id


def test_farm_draft_uses_completed_field_history_and_crop_constraints(session):
    field_id = onboard(
        session,
        allowed_crops=["Wheat", "Barley", "Peas"],
        priority="soil",
        history=[
            {"season": 2021, "crop": "Peas"},
            {"season": 2025, "crop": None},
            {"season": 2026, "crop": "Wheat"},
        ],
    )
    field = next(item for item in planning_context(session, 2027).fields if item.id == field_id)
    assert [(entry.season, entry.crop) for entry in field.history] == [
        (2021, "Peas"),
        (2026, "Wheat"),
    ]
    assert field.allowed_crops == ["Wheat", "Barley", "Peas"]
    assert field.priority == "soil"

    # Farm-wide rules include Soy, preferred by the farm but not allowed on this field.
    request = PlanningRequest.model_validate(
        {
            "client_id": uuid4(),
            "start_season": 2027,
            "field_ids": [field_id, "F-01"],
            "crops": [
                {"crop": "Soy", "preference": 10},
                {"crop": "Peas", "min_break_years": 8, "preference": 9},
                {"crop": "Wheat"},
                {"crop": "Barley"},
            ],
        }
    )
    draft = create_draft(session, request)
    assert draft.result.status == "optimal"
    assignments = [item for item in draft.result.assignments if item.field_id == field_id]
    assert [(item.season, item.crop) for item in assignments] == [
        (2027, "Barley"),
        (2028, "Wheat"),
        (2029, "Barley"),
    ]
    assert any(item.field_id == "F-01" and item.crop == "Soy" for item in draft.result.assignments)
    assert apply_draft(session, draft.id).applied_at is not None
    schedules, _ = get_plan_snapshot(session, session.get(Field, field_id), 2026)
    assert [item.crop for item in schedules[0].crops] == ["Barley", "Wheat", "Barley"]


def test_default_comparison_uses_onboarding_and_preserves_one_valid_sequence(session):
    field_id = onboard(
        session,
        allowed_crops=["Wheat", "Barley"],
        priority="water",
        history=[{"season": 2026, "crop": "Wheat"}],
    )
    schedules, main = get_plan_snapshot(session, session.get(Field, field_id), 2026)
    assert main == "plan-a"
    assert len(schedules) == 1
    with pytest.raises(ConflictError, match="crop sequence is unavailable"):
        set_main(session, MainPlanUpdate(field_id=field_id, season=2026, plan_id="plan-b"))
    session.rollback()
    assert [(item.year, item.crop) for item in schedules[0].crops] == [
        (2027, "Barley"),
        (2028, "Wheat"),
        (2029, "Barley"),
    ]

    # In a later season the last known crop is not assumed to still be planted.
    future, _ = get_plan_snapshot(session, session.get(Field, field_id), 2027)
    assert len(future) == 2
    assert [item.crop for item in future[0].crops] == ["Wheat", "Barley", "Wheat"]
