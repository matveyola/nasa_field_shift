"""Replace unchanged illustrative demo boundaries with georeferenced NAIP traces."""

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from field_shift.demo_fields_naip import DEMO_FIELDS_NAIP_2026

revision = "0003_naip_demo_fields"
down_revision = "0002_jobs"
branch_labels = None
depends_on = None

OLD = {
    "F-01": (
        24.6,
        [
            [30.4620, 50.2545],
            [30.4670, 50.2580],
            [30.4735, 50.2572],
            [30.4754, 50.2528],
            [30.4708, 50.2516],
            [30.4620, 50.2545],
        ],
    ),
    "F-02": (
        18.9,
        [
            [30.4758, 50.2527],
            [30.4784, 50.2570],
            [30.4862, 50.2550],
            [30.4839, 50.2473],
            [30.4782, 50.2490],
            [30.4758, 50.2527],
        ],
    ),
    "F-03": (
        32.1,
        [
            [30.4660, 50.2469],
            [30.4711, 50.2510],
            [30.4780, 50.2488],
            [30.4798, 50.2468],
            [30.4834, 50.2455],
            [30.4801, 50.2411],
            [30.4762, 50.2422],
            [30.4729, 50.2421],
            [30.4693, 50.2452],
            [30.4660, 50.2469],
        ],
    ),
}
FIELDS = sa.table(
    "fields",
    sa.column("id", sa.String()),
    sa.column("farm_id", sa.Integer()),
    sa.column("name", sa.String()),
    sa.column("crop", sa.String()),
    sa.column("season", sa.Integer()),
    sa.column("area_ha", sa.Float()),
    sa.column("geometry", sa.JSON()),
)


def _polygon(ring: list) -> dict:
    return {"type": "Polygon", "coordinates": [ring]}


def _replace(restore: bool) -> None:
    is_postgres = op.get_context().dialect.name == "postgresql"

    def comparable(geometry):
        return sa.cast(geometry, JSONB()) if is_postgres else sa.func.json(geometry)

    for field_id, name, crop, area, ring in DEMO_FIELDS_NAIP_2026:
        old_area, old_ring = OLD[field_id]
        source_area, target_area = (area, old_area) if restore else (old_area, area)
        source_ring, target_ring = (ring, old_ring) if restore else (old_ring, ring)
        accepted = [_polygon(source_ring)]
        if not restore and field_id == "F-01":
            # An earlier local seed used this vertex before its overlap was corrected.
            accepted.append(_polygon([*old_ring[:4], [30.4708, 50.2492], old_ring[-1]]))
        replacement = sa.literal(json.dumps(_polygon(target_ring)))
        stored = sa.cast(replacement, sa.JSON()) if is_postgres else sa.func.json(replacement)
        op.execute(
            sa.update(FIELDS)
            .where(
                FIELDS.c.id == field_id,
                FIELDS.c.farm_id == 1,
                FIELDS.c.name == name,
                FIELDS.c.crop == crop,
                FIELDS.c.season == 2026,
                FIELDS.c.area_ha == source_area,
                comparable(FIELDS.c.geometry).in_(
                    [comparable(sa.literal(json.dumps(geometry))) for geometry in accepted]
                ),
            )
            .values(area_ha=target_area, geometry=stored)
        )


def upgrade() -> None:
    _replace(restore=False)


def downgrade() -> None:
    _replace(restore=True)
