"""Save voluntarily completed field strategy scenarios."""

import sqlalchemy as sa
from alembic import op

revision = "0017_strategy_scenarios"
down_revision = "0016_merge_agent_planning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_scenarios",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("field_id", sa.String(24), nullable=False),
        sa.Column("geometry_revision", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["field_id"],
            ["fields.id"],
            name=op.f("fk_strategy_scenarios_field_id_fields"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_strategy_scenarios")),
        sa.UniqueConstraint(
            "field_id",
            "geometry_revision",
            "fingerprint",
            name=op.f("uq_strategy_scenarios_field_id"),
        ),
    )
    op.create_index(
        "ix_strategy_scenarios_field_revision",
        "strategy_scenarios",
        ["field_id", "geometry_revision"],
    )


def downgrade() -> None:
    op.drop_index("ix_strategy_scenarios_field_revision", table_name="strategy_scenarios")
    op.drop_table("strategy_scenarios")
