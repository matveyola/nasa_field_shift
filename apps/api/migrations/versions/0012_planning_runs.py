"""Durable immutable scenario runs, human applies and crop commitments."""

import sqlalchemy as sa
from alembic import op

revision = "0012_planning_runs"
down_revision = "0010_precision_plans"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "planning_v1_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("farm_id", sa.Integer(), sa.ForeignKey("farms.id"), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("artifacts", sa.JSON()),
        sa.Column("lineage", sa.JSON()),
        sa.Column("versions", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("farm_id", "request_id"),
    )
    op.create_index("ix_planning_v1_runs_farm_id", "planning_v1_runs", ["farm_id"])
    op.create_table(
        "planning_v1_applies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("farm_id", sa.Integer(), sa.ForeignKey("farms.id"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("planning_v1_runs.id"), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("alternative_id", sa.String(128), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.UniqueConstraint("farm_id", "request_id"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index("ix_planning_v1_applies_farm_id", "planning_v1_applies", ["farm_id"])
    op.create_table(
        "planning_v1_commitments",
        sa.Column("farm_id", sa.Integer(), sa.ForeignKey("farms.id"), primary_key=True),
        sa.Column("field_id", sa.String(24), sa.ForeignKey("fields.id"), primary_key=True),
        sa.Column("season", sa.Integer(), primary_key=True),
        sa.Column("commitment_id", sa.String(128), nullable=False, unique=True),
        sa.Column("assignment", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("planning_v1_commitments")
    op.drop_table("planning_v1_applies")
    op.drop_table("planning_v1_runs")
