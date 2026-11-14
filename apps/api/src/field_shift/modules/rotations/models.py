from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from field_shift.db import Base


class RotationPreference(Base):
    __tablename__ = "rotation_preferences"

    field_id: Mapped[str] = mapped_column(ForeignKey("fields.id"), primary_key=True)
    season: Mapped[int] = mapped_column(primary_key=True)
    main_plan_id: Mapped[str] = mapped_column(String(20))
    plan_a_crops: Mapped[list[str] | None] = mapped_column(JSON(none_as_null=True))
    plan_b_crops: Mapped[list[str] | None] = mapped_column(JSON(none_as_null=True))


class FarmRotationDraft(Base):
    __tablename__ = "farm_rotation_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    client_id: Mapped[str] = mapped_column(String(36), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    start_season: Mapped[int]
    request: Mapped[dict] = mapped_column(JSON)
    input_snapshot: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
