"""Team accounts, revocable sessions and invitations.

Revision ID: 0008_team_access
Revises: 0007_field_boundary_history
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_team_access"
down_revision = "0007_field_boundary_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "access_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "access_memberships",
        sa.Column("farm_id", sa.Integer(), sa.ForeignKey("farms.id"), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("access_users.id"), primary_key=True),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.CheckConstraint("role IN ('admin', 'editor', 'viewer')", name="role"),
    )
    op.create_table(
        "access_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("access_users.id"), nullable=False),
        sa.Column("farm_id", sa.Integer(), sa.ForeignKey("farms.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_access_sessions_user_id", "access_sessions", ["user_id"])
    op.create_index("ix_access_sessions_expires_at", "access_sessions", ["expires_at"])
    op.create_table(
        "access_invitations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("farm_id", sa.Integer(), sa.ForeignKey("farms.id"), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("role IN ('admin', 'editor', 'viewer')", name="role"),
    )
    op.create_index("ix_access_invitations_farm_id", "access_invitations", ["farm_id"])
    op.create_table(
        "access_throttles",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )
    op.create_index("ix_access_throttles_window_start", "access_throttles", ["window_start"])


def downgrade() -> None:
    op.drop_table("access_throttles")
    op.drop_table("access_invitations")
    op.drop_table("access_sessions")
    op.drop_table("access_memberships")
    op.drop_table("access_users")
