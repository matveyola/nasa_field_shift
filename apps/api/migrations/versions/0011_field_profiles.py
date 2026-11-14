"""Persist immutable field planning profiles and crop history."""

import sqlalchemy as sa
from alembic import op

revision = "0011_field_profiles"
down_revision = "0010_precision_plans"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "planning_field_profiles",
        sa.Column("snapshot_id", sa.String(128), primary_key=True),
        sa.Column("farm_id", sa.Integer(), sa.ForeignKey("farms.id"), nullable=False),
        sa.Column("field_id", sa.String(24), sa.ForeignKey("fields.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("geometry_revision", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.UniqueConstraint("field_id", "revision"),
        sa.UniqueConstraint("farm_id", "request_id"),
    )
    op.create_index("ix_planning_field_profiles_field_id", "planning_field_profiles", ["field_id"])

    op.create_table(
        "planning_weather_issues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("farm_id", sa.Integer(), sa.ForeignKey("farms.id"), nullable=False),
        sa.Column("scenario_set_id", sa.String(128), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.UniqueConstraint("farm_id", "scenario_set_id"),
    )


def downgrade():
    op.drop_table("planning_weather_issues")
    op.drop_table("planning_field_profiles")
