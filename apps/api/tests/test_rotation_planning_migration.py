from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from field_shift.db import build_engine
from field_shift.modules.fields.models import Field
from field_shift.modules.rotations.models import RotationPreference
from field_shift.modules.rotations.planning_schemas import PlanningRequest
from field_shift.modules.rotations.planning_service import create_draft
from field_shift.seed import seed_demo


def test_rotation_migration_preserves_fields_and_existing_preferences(tmp_path: Path):
    api = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'rotation.sqlite3').as_posix()}"
    config = Config(str(api / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0008_team_access")
    engine = build_engine(url)
    try:
        with Session(engine) as session:
            seed_demo(session)
            session.add(
                RotationPreference(
                    field_id="F-01",
                    season=2026,
                    main_plan_id="plan-b",
                    plan_b_crops=["Soy", "Wheat", "Maize"],
                )
            )
            session.commit()
        command.upgrade(config, "0009_rotation_planning")
        with Session(engine) as session:
            plan = create_draft(
                session,
                PlanningRequest.model_validate(
                    {
                        "client_id": "7b7ce456-b7e2-4f9c-b90c-880b2046219d",
                        "start_season": 2027,
                        "field_ids": ["F-01"],
                        "crops": [{"crop": "Soy"}, {"crop": "Wheat"}],
                    }
                ),
            )
            assert plan.result.status == "optimal"
            assert session.get(Field, "F-01").name == "West Field"
            assert session.get(RotationPreference, ("F-01", 2026)).main_plan_id == "plan-b"
        command.downgrade(config, "0008_team_access")
        assert "farm_rotation_drafts" not in inspect(engine).get_table_names()
        with Session(engine) as session:
            assert session.get(Field, "F-01").name == "West Field"
            assert session.get(RotationPreference, ("F-01", 2026)).plan_b_crops == [
                "Soy",
                "Wheat",
                "Maize",
            ]
    finally:
        engine.dispose()
