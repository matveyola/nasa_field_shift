"""Immutable operator inputs and the farm's active version pointer."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from field_shift.db import Base


class PlanningBundle(Base):
    __tablename__ = "planning_input_bundles"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    document: Mapped[dict] = mapped_column(JSON)
    model_card: Mapped[dict] = mapped_column(JSON)


class PlanningHead(Base):
    __tablename__ = "planning_input_heads"
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), primary_key=True)
    bundle_id: Mapped[str] = mapped_column(ForeignKey("planning_input_bundles.id"))


class PlanningDelivery(Base):
    __tablename__ = "planning_deliveries"
    run_id: Mapped[str] = mapped_column(ForeignKey("planning_v1_runs.id"), primary_key=True)
    next_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
