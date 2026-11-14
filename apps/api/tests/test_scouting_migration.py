from datetime import date
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, insert, select
from sqlalchemy.orm import Session

from field_shift.db import build_engine
from field_shift.modules.farm.models import Farm, Observation
from field_shift.modules.farm.schemas import ObservationRead
from field_shift.modules.fields.models import Field


def test_scouting_migration_preserves_legacy_notes(tmp_path: Path) -> None:
    api = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'scouting.sqlite3').as_posix()}"
    config = Config(str(api / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0005_agent_reply_metadata")
    engine = build_engine(url)
    note = {
        "id": 1,
        "field_id": "F-01",
        "kind": "field_note",
        "text": "Original observation.",
        "date": date(2026, 8, 1),
    }
    try:
        with engine.begin() as connection:
            connection.execute(
                insert(Farm.__table__).values(
                    id=1,
                    name="Test farm",
                    season=2026,
                    next_field_number=2,
                    irrigation_available=False,
                    priority="Planning",
                )
            )
            connection.execute(
                insert(Field.__table__).values(
                    id="F-01",
                    farm_id=1,
                    name="Test field",
                    season=2026,
                    crop="Soy",
                    area_ha=10,
                    boundary_revision=0,
                    geometry={
                        "type": "Polygon",
                        "coordinates": [[[31, 48], [31.1, 48], [31, 48.1], [31, 48]]],
                    },
                )
            )
            legacy_observations = Table("observations", MetaData(), autoload_with=connection)
            connection.execute(insert(legacy_observations).values(**note))
        command.upgrade(config, "0006_field_scouting")
        with Session(engine) as session:
            assert ObservationRead.model_validate(session.get(Observation, 1)).model_dump() == note
        command.downgrade(config, "0005_agent_reply_metadata")
        with engine.connect() as connection:
            assert dict(connection.execute(select(legacy_observations)).mappings().one()) == note
    finally:
        engine.dispose()
