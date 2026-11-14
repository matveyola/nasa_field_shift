"""Append-only seasonal boundary snapshots and public prepared contour catalogue."""

import sqlalchemy as sa
from alembic import op

revision = "0007_field_boundary_history"
down_revision = "0006_field_scouting"
branch_labels = None
depends_on = None


def upgrade():
    versions = op.create_table(
        "field_boundary_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "field_id",
            sa.String(24),
            sa.ForeignKey("fields.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("effective_season", sa.Integer(), nullable=False),
        sa.Column("geometry", sa.JSON(), nullable=False),
        sa.Column("area_ha", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.UniqueConstraint("field_id", "revision", name="uq_field_boundary_versions_field_id"),
    )
    op.create_index("ix_field_boundary_versions_field_id", "field_boundary_versions", ["field_id"])
    fields = sa.table(
        "fields",
        sa.column("id"),
        sa.column("boundary_revision"),
        sa.column("season"),
        sa.column("geometry", sa.JSON()),
        sa.column("area_ha"),
    )
    op.get_bind().execute(
        versions.insert().from_select(
            [
                "field_id",
                "revision",
                "effective_season",
                "geometry",
                "area_ha",
                "created_at",
                "source",
            ],
            sa.select(
                fields.c.id,
                fields.c.boundary_revision,
                fields.c.season,
                fields.c.geometry,
                fields.c.area_ha,
                sa.func.current_timestamp(),
                sa.literal("migration_baseline"),
            ),
        )
    )
    op.create_table(
        "prepared_contours",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("geometry", sa.JSON(), nullable=False),
        sa.Column("area_ha", sa.Float(), nullable=False),
        *[
            sa.Column(name, sa.Float(), nullable=False)
            for name in ("west", "east", "south", "north")
        ],
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("source_url", sa.String(400), nullable=False),
        sa.Column("license", sa.String(80), nullable=False),
        sa.Column("region", sa.String(80), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_prepared_contours_bbox", "prepared_contours", ["west", "east", "south", "north"]
    )


def downgrade():
    op.drop_table("prepared_contours")
    op.drop_table("field_boundary_versions")
