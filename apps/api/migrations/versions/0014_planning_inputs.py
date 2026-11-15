"""Pin operator-reviewed inputs and model identity for durable planning."""

import sqlalchemy as sa
from alembic import op

revision = "0014_planning_inputs"
down_revision = "0013_merge_planning"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "planning_input_bundles",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("farm_id", sa.Integer(), sa.ForeignKey("farms.id"), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("model_card", sa.JSON(), nullable=False),
    )
    op.create_index("ix_planning_input_bundles_farm_id", "planning_input_bundles", ["farm_id"])
    op.create_table(
        "planning_input_heads",
        sa.Column("farm_id", sa.Integer(), sa.ForeignKey("farms.id"), primary_key=True),
        sa.Column(
            "bundle_id", sa.String(64), sa.ForeignKey("planning_input_bundles.id"), nullable=False
        ),
    )

    op.create_table(
        "planning_deliveries",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("planning_v1_runs.id"), primary_key=True),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_planning_deliveries_next_at", "planning_deliveries", ["next_at"])


def downgrade():
    op.drop_table("planning_deliveries")
    op.drop_table("planning_input_heads")
    op.drop_table("planning_input_bundles")
