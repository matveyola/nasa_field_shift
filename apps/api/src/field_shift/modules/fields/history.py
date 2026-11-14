"""Immutable boundary events; the current field remains the active-season projection."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from field_shift.modules.fields.models import Field, FieldBoundaryVersion


def versions(session: Session, field_id: str):
    return list(
        session.scalars(
            select(FieldBoundaryVersion)
            .where(FieldBoundaryVersion.field_id == field_id)
            .order_by(FieldBoundaryVersion.revision.desc())
            .limit(500)
        )
    )


def record_boundary(
    session: Session, field: Field, season: int, geometry: dict, area: float, source: str
):
    version = FieldBoundaryVersion(
        field_id=field.id,
        revision=field.boundary_revision,
        effective_season=season,
        geometry=geometry,
        area_ha=area,
        created_at=datetime.now(UTC),
        source=source,
    )
    session.add(version)
    session.flush()
    return version


def ensure_baseline(session: Session, field: Field):
    if (
        session.scalar(
            select(FieldBoundaryVersion.id)
            .where(FieldBoundaryVersion.field_id == field.id)
            .limit(1)
        )
        is None
    ):
        record_boundary(
            session, field, field.season, field.geometry, field.area_ha, "legacy_baseline"
        )


def boundary_for_season(session: Session, field: Field, season: int):
    record = session.scalar(
        select(FieldBoundaryVersion)
        .where(
            FieldBoundaryVersion.field_id == field.id,
            FieldBoundaryVersion.effective_season <= season,
        )
        .order_by(
            FieldBoundaryVersion.effective_season.desc(), FieldBoundaryVersion.revision.desc()
        )
        .limit(1)
    )
    if record is not None:
        return record.geometry, record.area_ha, False
    # Existing datasets may predate imported history. Never invent a historical event.
    earliest = session.scalar(
        select(FieldBoundaryVersion)
        .where(
            FieldBoundaryVersion.field_id == field.id,
        )
        .order_by(FieldBoundaryVersion.effective_season, FieldBoundaryVersion.revision)
        .limit(1)
    )
    return (
        (earliest.geometry, earliest.area_ha, True)
        if earliest
        else (field.geometry, field.area_ha, True)
    )
