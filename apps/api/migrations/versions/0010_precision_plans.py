"""Store immutable application map and trial snapshots, approvals and harvest results."""

import sqlalchemy as sa
from alembic import op

revision = "0010_precision_plans"
down_revision = "0009_rotation_planning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "precision_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("field_id", sa.String(24), sa.ForeignKey("fields.id"), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("boundary_revision", sa.Integer(), nullable=False),
        sa.Column("geometry_fingerprint", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("harvest", sa.JSON()),
        sa.Column("harvest_hash", sa.String(64)),
        sa.Column("harvest_name", sa.Text()),
    )
    op.create_index("ix_precision_plans_field_id", "precision_plans", ["field_id"])


def downgrade() -> None:
    op.drop_table("precision_plans")
