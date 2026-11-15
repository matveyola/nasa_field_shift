"""Lane-owned persistence. Register these tables at the integration boundary."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from field_shift.db import Base


class PlanningRunRow(Base):
    __tablename__ = "planning_v1_runs"
    __table_args__ = (UniqueConstraint("farm_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    input_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    artifacts: Mapped[dict | None] = mapped_column(JSON)
    lineage: Mapped[dict | None] = mapped_column(JSON)
    versions: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16))
    attempt: Mapped[int]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PlanningApplyRow(Base):
    __tablename__ = "planning_v1_applies"
    __table_args__ = (
        UniqueConstraint("farm_id", "request_id"),
        UniqueConstraint("run_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("planning_v1_runs.id"))
    request_id: Mapped[str] = mapped_column(String(128))
    input_hash: Mapped[str] = mapped_column(String(64))
    alternative_id: Mapped[str] = mapped_column(String(128))
    result: Mapped[dict] = mapped_column(JSON)


class PlanningCommitment(Base):
    __tablename__ = "planning_v1_commitments"
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), primary_key=True)
    field_id: Mapped[str] = mapped_column(ForeignKey("fields.id"), primary_key=True)
    season: Mapped[int] = mapped_column(primary_key=True)
    commitment_id: Mapped[str] = mapped_column(String(128), unique=True)
    assignment: Mapped[dict] = mapped_column(JSON)
