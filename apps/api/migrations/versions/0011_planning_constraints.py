"""Store farmer decisions about assistant-proposed planning constraints, per field and season."""

import sqlalchemy as sa
from alembic import op

revision = "0011_planning_constraints"
down_revision = "0010_precision_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "field_planning_constraints",
        sa.Column(
            "field_id",
            sa.String(24),
            sa.ForeignKey("fields.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("season", sa.Integer(), primary_key=True),
        sa.Column("type", sa.String(32), primary_key=True),
        sa.Column("boundary_revision", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("evidence", sa.String(500)),
        sa.Column("confidence", sa.Float()),
        sa.Column("decided_by", sa.Integer()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('confirmed', 'rejected')", name=op.f("ck_field_planning_constraints_status")
        ),
        sa.CheckConstraint(
            "source IN ('farmer_text', 'farmer_voice', 'farmer_ui', 'agent_inferred')",
            name=op.f("ck_field_planning_constraints_source"),
        ),
    )


def downgrade() -> None:
    op.drop_table("field_planning_constraints")
