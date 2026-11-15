from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from field_shift.db import build_engine
from field_shift.modules.fields.models import Field
from field_shift.modules.onboarding.schemas import SetupInputs
from field_shift.modules.onboarding.service import save_setup
from field_shift.seed import seed_demo


def test_onboarding_migration_preserves_fields_and_can_be_reversed(tmp_path: Path):
    api = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'migration.sqlite').as_posix()}"
    config = Config(str(api / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0010_precision_plans")
    engine = build_engine(url)
    try:
        with Session(engine) as session:
            seed_demo(session)
        command.upgrade(config, "0011_field_onboarding")
        with Session(engine) as session:
            saved = save_setup(
                session,
                UUID("e3121f4c-acf6-4481-82a5-5ad756187a56"),
                SetupInputs(country_code="UA"),
            )
            assert saved.status == "draft"
            assert session.get(Field, "F-01").name == "West Field"
        command.downgrade(config, "0010_precision_plans")
        assert "field_setups" not in inspect(engine).get_table_names()
        with Session(engine) as session:
            assert session.get(Field, "F-01").name == "West Field"
    finally:
        engine.dispose()


@pytest.mark.parametrize("published_head", ["0011_field_onboarding", "0014_planning_inputs"])
def test_merge_preserves_both_published_migration_histories(tmp_path, published_head):
    api = Path(__file__).resolve().parents[1]
    config = Config(str(api / "alembic.ini"))
    url = f"sqlite:///{tmp_path / 'both-histories.sqlite'}"
    config.attributes["database_url"] = url
    command.upgrade(config, published_head)
    engine = build_engine(url)
    try:
        with Session(engine) as session:
            seed_demo(session)
        command.upgrade(config, "head")
        tables = set(inspect(engine).get_table_names())
        assert {"field_setups", "planning_input_bundles", "farm_rotation_drafts"} <= tables
        with Session(engine) as session:
            assert session.get(Field, "F-01").name == "West Field"
        command.upgrade(config, "head")
    finally:
        engine.dispose()
