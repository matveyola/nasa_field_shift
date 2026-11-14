from sqlalchemy import and_, select, update
from sqlalchemy.orm import Session

from field_shift.errors import ConflictError, NotFoundError
from field_shift.modules.farm.models import FARM_ID, Farm
from field_shift.modules.fields.geometry import area_hectares, covers_ring, same_boundary
from field_shift.modules.fields.history import boundary_for_season, ensure_baseline, record_boundary
from field_shift.modules.fields.models import Field, FieldPlanting
from field_shift.modules.fields.schemas import (
    FieldBoundaryUpdate,
    FieldCreate,
    FieldPlantingRead,
    FieldPlantingUpdate,
    FieldRead,
    Polygon,
)
from field_shift.modules.onboarding.profiles import completed_setup_inputs


def require_field(session: Session, field_id: str) -> Field:
    field = session.get(Field, field_id)
    if field is None or field.farm_id != FARM_ID:
        raise NotFoundError(f"Field {field_id} was not found")
    return field


def field_read(
    field: Field,
    *,
    planting: FieldPlanting | None = None,
    irrigation_available: bool | None = None,
) -> FieldRead:
    return FieldRead.model_validate(field).model_copy(
        update={
            "planting": FieldPlantingRead.model_validate(planting) if planting else None,
            "irrigation_available": irrigation_available,
        }
    )


def _field_irrigation_override(session: Session, field_id: str) -> bool | None:
    profile = completed_setup_inputs(session, (field_id,)).get(field_id)
    return profile.irrigation if profile is not None else None


def list_fields(session: Session) -> list[FieldRead]:
    fields = session.execute(
        select(Field, FieldPlanting)
        .join(Farm, Field.farm_id == Farm.id)
        .outerjoin(
            FieldPlanting,
            and_(FieldPlanting.field_id == Field.id, FieldPlanting.season == Farm.season),
        )
        .where(Field.farm_id == FARM_ID)
        .order_by(Field.id)
    ).all()
    profiles = completed_setup_inputs(session, (field.id for field, _ in fields))
    return [
        field_read(
            field,
            planting=planting,
            irrigation_available=profiles[field.id].irrigation if field.id in profiles else None,
        )
        for field, planting in fields
    ]


def _lock_field(session: Session, field_id: str, boundary_revision: int) -> tuple[Field, int]:
    row = session.execute(
        select(Field, Farm.season)
        .join(Farm, Field.farm_id == Farm.id)
        .where(Field.id == field_id, Field.farm_id == FARM_ID)
        .with_for_update(of=Field)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        raise NotFoundError(f"Field {field_id} was not found")
    field, season = row
    if field.boundary_revision != boundary_revision:
        raise ConflictError("The field boundary changed. Reload the field before saving.")
    return field, season


def create_field(session: Session, payload: FieldCreate, *, commit: bool = True) -> Field:
    sequence = session.execute(
        update(Farm)
        .where(Farm.id == FARM_ID)
        .values(next_field_number=Farm.next_field_number + 1)
        .returning(Farm.next_field_number, Farm.season)
    ).one_or_none()
    if sequence is None:
        raise NotFoundError("Farm was not found")
    field = Field(
        id=f"F-{sequence.next_field_number - 1:02d}",
        farm_id=FARM_ID,
        name=payload.name,
        crop=payload.crop,
        area_ha=payload.area_ha,
        season=payload.season if payload.season is not None else sequence.season,
        geometry=payload.geometry.model_dump(mode="json"),
    )
    session.add(field)
    session.flush()
    record_boundary(session, field, field.season, field.geometry, field.area_ha, "created")
    if commit:
        session.commit()
    return field


def update_boundary(session: Session, field_id: str, payload: FieldBoundaryUpdate) -> FieldRead:
    field, season = _lock_field(session, field_id, payload.boundary_revision)
    effective = payload.effective_season if payload.effective_season is not None else season
    if effective > season:
        raise ConflictError("Boundary history cannot be dated after the active farm season.")
    ensure_baseline(session, field)
    planting = session.get(FieldPlanting, (field.id, effective))
    current_geometry, _, extrapolated = boundary_for_season(session, field, effective)
    current = Polygon.model_validate(current_geometry).coordinates[0]
    boundary = payload.geometry.coordinates[0]
    if extrapolated or not same_boundary(current, boundary):
        if planting and not covers_ring(
            boundary, Polygon.model_validate(planting.geometry).coordinates[0]
        ):
            raise ConflictError(
                "The new boundary would exclude part of the planted area. "
                "Edit the planted area first."
            )
        field.boundary_revision += 1
        record_boundary(
            session,
            field,
            effective,
            payload.geometry.model_dump(mode="json"),
            area_hectares(boundary),
            "edited",
        )
        for historical_planting in session.scalars(
            select(FieldPlanting).where(FieldPlanting.field_id == field.id)
        ):
            historical_boundary, _, _ = boundary_for_season(
                session, field, historical_planting.season
            )
            if not covers_ring(
                Polygon.model_validate(historical_boundary).coordinates[0],
                Polygon.model_validate(historical_planting.geometry).coordinates[0],
            ):
                raise ConflictError("The seasonal boundary would exclude a saved planted area.")
        field.geometry, field.area_ha, _ = boundary_for_season(session, field, season)
    result = field_read(
        field,
        planting=session.get(FieldPlanting, (field.id, season)),
        irrigation_available=_field_irrigation_override(session, field.id),
    )
    session.commit()
    return result


def update_planting(
    session: Session, field_id: str, season: int, payload: FieldPlantingUpdate
) -> FieldRead:
    field, active_season = _lock_field(session, field_id, payload.boundary_revision)
    if season != active_season:
        raise ConflictError("Only the active farm season can be edited.")

    planting = session.get(FieldPlanting, (field.id, season))
    if payload.geometry is None:
        if planting:
            session.delete(planting)
        planting = None
    else:
        boundary = Polygon.model_validate(field.geometry).coordinates[0]
        footprint = payload.geometry.coordinates[0]
        if not covers_ring(boundary, footprint):
            raise ConflictError("The planted area must be within the field boundary.")
        if planting is None:
            planting = FieldPlanting(field_id=field.id, season=season)
            session.add(planting)
        planting.geometry = payload.geometry.model_dump(mode="json")
        planting.area_ha = area_hectares(footprint)

    result = field_read(
        field, planting=planting, irrigation_available=_field_irrigation_override(session, field.id)
    )
    session.commit()
    return result
