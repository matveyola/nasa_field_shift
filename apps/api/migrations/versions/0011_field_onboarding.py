"""Persist resumable field setup and explicitly labeled demo rotation snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0011_field_onboarding"
down_revision = "0010_precision_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "field_setups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("farm_id", sa.Integer(), sa.ForeignKey("farms.id"), nullable=False),
        sa.Column("field_id", sa.String(24), sa.ForeignKey("fields.id"), unique=True),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("demo_plan", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_field_setups_farm_id", "field_setups", ["farm_id"])


def downgrade() -> None:
    op.drop_table("field_setups")
