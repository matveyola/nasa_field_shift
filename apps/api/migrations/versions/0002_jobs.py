"""Persist background job requests, results and retry state."""

import sqlalchemy as sa
from alembic import op

revision = "0002_jobs"
down_revision = "0001_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("field_id", sa.String(64), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("attempt_token", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatch_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatch_count", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(none_as_null=True)),
        sa.Column("error", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
        sa.CheckConstraint("kind IN ('simulation', 'geo_ml')", name=op.f("ck_jobs_kind")),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'retrying', 'succeeded', 'failed')",
            name=op.f("ck_jobs_status"),
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND max_attempts > 0 AND attempts <= max_attempts",
            name=op.f("ck_jobs_attempts"),
        ),
        sa.CheckConstraint(
            "(status = 'running' AND attempt_token IS NOT NULL AND lease_expires_at IS NOT NULL)"
            " OR (status <> 'running' AND attempt_token IS NULL AND lease_expires_at IS NULL)",
            name=op.f("ck_jobs_lease"),
        ),
    )
    op.create_index("ix_jobs_dispatch", "jobs", ["status", "dispatch_after", "available_at"])
    op.create_index("ix_jobs_lease", "jobs", ["status", "lease_expires_at"])
    op.create_index("ix_jobs_field_created", "jobs", ["field_id", "created_at"])
    op.create_index("ix_jobs_created", "jobs", ["created_at"])


def downgrade() -> None:
    op.drop_table("jobs")
