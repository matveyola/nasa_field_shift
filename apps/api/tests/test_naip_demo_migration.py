from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import insert, select

from field_shift.db import build_engine
from field_shift.demo_fields_naip import DEMO_FIELDS_NAIP_2026
from field_shift.modules.farm.models import Farm
from field_shift.modules.fields.models import Field

OLD_WEST = [
    [30.4620, 50.2545],
    [30.4670, 50.2580],
    [30.4735, 50.2572],
    [30.4754, 50.2528],
    [30.4708, 50.2516],
    [30.4620, 50.2545],
]
MANUAL_EAST = [
    [30.4758, 50.2527],
    [30.4784, 50.2570],
    [30.4862, 50.2550],
    [30.4758, 50.2527],
]


def polygon(ring: list) -> dict:
    return {"type": "Polygon", "coordinates": [ring]}


def test_migration_updates_only_unchanged_demo_fields_and_can_restore_them(tmp_path) -> None:
    api = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'migration.sqlite3').as_posix()}"
    config = Config(str(api / "alembic.ini"))
    config.set_main_option("script_location", str(api / "migrations"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0002_jobs")
    engine = build_engine(url)
    field = Field.__table__
    with engine.begin() as connection:
        connection.execute(
            insert(Farm.__table__).values(
                id=1,
                name="Oakridge Farm",
                season=2026,
                next_field_number=4,
                irrigation_available=False,
                priority="Reduce water stress",
            )
        )
        connection.execute(
            insert(field),
            [
                {
                    "id": "F-01",
                    "farm_id": 1,
                    "name": "West Field",
                    "crop": "Wheat",
                    "season": 2026,
                    "area_ha": 24.6,
                    "geometry": polygon(OLD_WEST),
                },
                {
                    "id": "F-02",
                    "farm_id": 1,
                    "name": "East Field",
                    "crop": "Soy",
                    "season": 2026,
                    "area_ha": 18.9,
                    "geometry": polygon(MANUAL_EAST),
                },
            ],
        )
    command.upgrade(config, "0003_naip_demo_fields")
    read = select(field.c.area_ha, field.c.geometry).order_by(field.c.id)
    with engine.connect() as connection:
        west, east = connection.execute(read).all()
        assert west.area_ha == DEMO_FIELDS_NAIP_2026[0][3]
        assert west.geometry == polygon(DEMO_FIELDS_NAIP_2026[0][4])
        assert east.geometry == polygon(MANUAL_EAST)
    command.downgrade(config, "0002_jobs")
    with engine.connect() as connection:
        west, east = connection.execute(read).all()
        assert west.area_ha == 24.6
        assert west.geometry == polygon(OLD_WEST)
        assert east.geometry == polygon(MANUAL_EAST)
    engine.dispose()
