from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from field_shift.db import Base


class PrecisionPlan(Base):
    __tablename__ = "precision_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    field_id: Mapped[str] = mapped_column(ForeignKey("fields.id"), index=True)
    season: Mapped[int]
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    boundary_revision: Mapped[int]
    geometry_fingerprint: Mapped[str] = mapped_column(String(64))
    version: Mapped[int]
    status: Mapped[str] = mapped_column(String(16))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    harvest: Mapped[dict | None] = mapped_column(JSON)
    harvest_hash: Mapped[str | None] = mapped_column(String(64))
    harvest_name: Mapped[str | None] = mapped_column(Text)
