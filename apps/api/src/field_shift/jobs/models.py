from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import ColumnElement

from field_shift.db import Base

BOUNDARY_REVISION_INPUT = "_boundary_revision"


class JobKind(StrEnum):
    SIMULATION = "simulation"
    GEO_ML = "geo_ml"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
        CheckConstraint("kind IN ('simulation', 'geo_ml')", name="kind"),
        CheckConstraint(
            "status IN ('queued', 'running', 'retrying', 'succeeded', 'failed')",
            name="status",
        ),
        CheckConstraint(
            "attempts >= 0 AND max_attempts > 0 AND attempts <= max_attempts",
            name="attempts",
        ),
        CheckConstraint(
            "(status = 'running' AND attempt_token IS NOT NULL AND lease_expires_at IS NOT NULL)"
            " OR (status <> 'running' AND attempt_token IS NULL AND lease_expires_at IS NULL)",
            name="lease",
        ),
        Index("ix_jobs_dispatch", "status", "dispatch_after", "available_at"),
        Index("ix_jobs_lease", "status", "lease_expires_at"),
        Index("ix_jobs_field_created", "field_id", "created_at"),
        Index("ix_jobs_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    field_id: Mapped[str] = mapped_column(String(64))
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer)
    max_attempts: Mapped[int] = mapped_column(Integer)
    attempt_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dispatch_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dispatch_count: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    error: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    @property
    def analysis(self) -> str | None:
        if self.kind == JobKind.SIMULATION:
            return self.inputs.get("analysis", "water_balance")
        return self.inputs.get("analysis")

    @property
    def boundary_revision(self) -> int:
        return self.inputs.get(BOUNDARY_REVISION_INPUT, 0)

    @classmethod
    def matches_boundary_revision(cls, revision: int) -> ColumnElement[bool]:
        return func.coalesce(cls.inputs[BOUNDARY_REVISION_INPUT].as_integer(), 0) == revision
