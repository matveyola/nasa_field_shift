from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from field_shift.db import Base


class StrategyScenario(Base):
    __tablename__ = "strategy_scenarios"
    __table_args__ = (
        UniqueConstraint("field_id", "geometry_revision", "fingerprint"),
        Index("ix_strategy_scenarios_field_revision", "field_id", "geometry_revision"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    field_id: Mapped[str] = mapped_column(ForeignKey("fields.id", ondelete="CASCADE"))
    geometry_revision: Mapped[int]
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    document: Mapped[dict] = mapped_column(JSON)
