from uuid import uuid4

import pytest

from field_shift.db import build_session_factory
from field_shift.models import metadata
from field_shift.modules.farm.service import get_farm
from field_shift.modules.fields.schemas import FieldBoundaryUpdate, FieldPlantingUpdate, Polygon
from field_shift.modules.fields.service import list_fields, update_boundary, update_planting
from field_shift.modules.onboarding.schemas import SetupInputs
from field_shift.modules.onboarding.service import complete_setup, save_setup
from field_shift.seed import seed_demo


@pytest.mark.parametrize("irrigation", [True, False, None])
def test_onboarding_irrigation_follows_field_across_workspace_reads(irrigation: bool | None):
    factory = build_session_factory("sqlite://")
    engine = factory.kw["bind"]
    metadata.create_all(engine)
    geometry = Polygon(
        type="Polygon",
        coordinates=[[(30.4, 50.2), (30.401, 50.2), (30.401, 50.201), (30.4, 50.2)]],
    )
    setup_id = uuid4()
    try:
        with factory() as session:
            seed_demo(session)
            save_setup(
                session,
                setup_id,
                SetupInputs(
                    country_code="UA",
                    name="Irrigation profile field",
                    geometry=geometry,
                    history=[],
                    irrigation=irrigation,
                    allowed_crops=["Wheat", "Peas"],
                    priority="soil",
                ),
            )
            completed = complete_setup(session, setup_id)
            assert completed.field is not None
            field_id = completed.field.id
            assert completed.field.irrigation_available is irrigation

        with factory() as session:
            fields = {field.id: field for field in list_fields(session)}
            assert fields[field_id].irrigation_available is irrigation
            assert fields["F-01"].irrigation_available is None
            assert get_farm(session).defaults.irrigation_available is False
            boundary = update_boundary(
                session, field_id, FieldBoundaryUpdate(geometry=geometry, boundary_revision=0)
            )
            assert boundary.irrigation_available is irrigation
            planting = update_planting(
                session,
                field_id,
                2026,
                FieldPlantingUpdate(
                    geometry=geometry, boundary_revision=boundary.boundary_revision
                ),
            )
            assert planting.irrigation_available is irrigation
            assert planting.planting is not None
    finally:
        engine.dispose()
