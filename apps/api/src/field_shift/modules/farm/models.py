from datetime import date

from sqlalchemy import CheckConstraint, ForeignKey, LargeBinary, String, Text, case
from sqlalchemy.orm import Mapped, column_property, mapped_column

from field_shift.db import Base

FARM_ID = 1


class Farm(Base):
    __tablename__ = "farms"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    season: Mapped[int]
    next_field_number: Mapped[int]
    irrigation_available: Mapped[bool]
    priority: Mapped[str] = mapped_column(String(120))
    equipment: Mapped[str | None] = mapped_column(String(240))
    budget: Mapped[float | None]


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (
        CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude IS NOT NULL AND longitude IS NOT NULL AND "
            "latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180)",
            name="coordinate_pair",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    field_id: Mapped[str] = mapped_column(ForeignKey("fields.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    date: Mapped[date]
    category: Mapped[str | None] = mapped_column(String(24))
    latitude: Mapped[float | None]
    longitude: Mapped[float | None]
    photo_jpeg: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    has_photo: Mapped[bool | None] = column_property(
        case((photo_jpeg.column.is_not(None), True), else_=None)
    )
    client_id: Mapped[str | None] = mapped_column(String(36), unique=True)
