from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from field_shift.db import Base


class Field(Base):
    __tablename__ = "fields"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    crop: Mapped[str] = mapped_column(String(80))
    area_ha: Mapped[float]
    season: Mapped[int]
    geometry: Mapped[dict] = mapped_column(JSON)
    boundary_revision: Mapped[int] = mapped_column(server_default=text("0"))


class FieldPlanting(Base):
    __tablename__ = "field_plantings"

    field_id: Mapped[str] = mapped_column(
        ForeignKey("fields.id", ondelete="CASCADE"), primary_key=True
    )
    season: Mapped[int] = mapped_column(primary_key=True)
    geometry: Mapped[dict] = mapped_column(JSON)
    area_ha: Mapped[float]


class FieldBoundaryVersion(Base):
    __tablename__ = "field_boundary_versions"
    __table_args__ = (UniqueConstraint("field_id", "revision"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    field_id: Mapped[str] = mapped_column(ForeignKey("fields.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int]
    effective_season: Mapped[int]
    geometry: Mapped[dict] = mapped_column(JSON)
    area_ha: Mapped[float]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(40))


class PreparedContour(Base):
    """Public catalogue, independent of private farmer field records."""

    __tablename__ = "prepared_contours"
    __table_args__ = (Index("ix_prepared_contours_bbox", "west", "east", "south", "north"),)

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    geometry: Mapped[dict] = mapped_column(JSON)
    area_ha: Mapped[float]
    west: Mapped[float]
    east: Mapped[float]
    south: Mapped[float]
    north: Mapped[float]
    year: Mapped[int]
    source: Mapped[str] = mapped_column(String(120))
    source_url: Mapped[str] = mapped_column(String(400))
    license: Mapped[str] = mapped_column(String(80))
    region: Mapped[str] = mapped_column(String(80))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
