"""Persist assistant provider, citations and clarification options."""

import sqlalchemy as sa
from alembic import op

revision = "0005_agent_reply_metadata"
down_revision = "0004_field_plantings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_messages", sa.Column("reply_metadata", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_messages", "reply_metadata")
