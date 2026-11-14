"""Persist whole-farm rotation drafts and their immutable input snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0009_rotation_planning"
down_revision = "0008_team_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "farm_rotation_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("farm_id", sa.Integer(), sa.ForeignKey("farms.id"), nullable=False),
        sa.Column("client_id", sa.String(36), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("start_season", sa.Integer(), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_farm_rotation_drafts_farm_id", "farm_rotation_drafts", ["farm_id"])


def downgrade() -> None:
    op.drop_index("ix_farm_rotation_drafts_farm_id", table_name="farm_rotation_drafts")
    op.drop_table("farm_rotation_drafts")
