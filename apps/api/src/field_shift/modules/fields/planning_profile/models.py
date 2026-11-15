"""Append-only snapshots; history is embedded in every immutable document."""

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from field_shift.db import Base


class ProfileSnapshot(Base):
    __tablename__ = "planning_field_profiles"
    __table_args__ = (
        UniqueConstraint("field_id", "revision"),
        UniqueConstraint("farm_id", "request_id"),
    )
    snapshot_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("farms.id"))
    field_id: Mapped[str] = mapped_column(ForeignKey("fields.id"), index=True)
    revision: Mapped[int]
    geometry_revision: Mapped[int]
    request_id: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    document: Mapped[dict] = mapped_column(JSON)
