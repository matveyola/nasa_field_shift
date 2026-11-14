from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from field_shift.db import Base


class FieldSetup(Base):
    __tablename__ = "field_setups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    field_id: Mapped[str | None] = mapped_column(ForeignKey("fields.id"), unique=True)
    inputs: Mapped[dict] = mapped_column(JSON)
    demo_plan: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
