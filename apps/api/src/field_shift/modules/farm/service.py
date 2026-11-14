from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from field_shift.errors import ConflictError, NotFoundError
from field_shift.modules.farm.models import FARM_ID, Farm, Observation
from field_shift.modules.farm.schemas import (
    DefaultsPatch,
    FarmDefaults,
    FarmRead,
    ObservationCreate,
    ObservationRead,
    ScoutingEvidence,
)
from field_shift.modules.fields.models import Field
from field_shift.modules.fields.service import require_field


def require_farm(session: Session) -> Farm:
    farm = session.get(Farm, FARM_ID)
    if farm is None:
        raise NotFoundError("Farm was not found")
    return farm


def get_farm(session: Session) -> FarmRead:
    farm = require_farm(session)
    area = session.scalar(select(func.sum(Field.area_ha)).where(Field.farm_id == farm.id))
    observations = session.scalars(
        select(Observation)
        .join(Field, Field.id == Observation.field_id)
        .where(Field.farm_id == farm.id)
        .order_by(Observation.date.desc(), Observation.id.desc())
    )
    return FarmRead(
        name=farm.name,
        season=farm.season,
        total_area_ha=round(area or 0, 2),
        defaults=FarmDefaults.model_validate(farm),
        observations=[ObservationRead.model_validate(observation) for observation in observations],
    )


def update_defaults(session: Session, patch: DefaultsPatch) -> FarmDefaults:
    farm = require_farm(session)
    for name, value in patch.model_dump(exclude_unset=True).items():
        setattr(farm, name, value)
    session.commit()
    return FarmDefaults.model_validate(farm)


def create_observation(session: Session, payload: ObservationCreate) -> Observation:
    require_field(session, payload.field_id)
    client_id = str(payload.client_id) if payload.client_id else None
    observation = Observation(
        field_id=payload.field_id,
        kind=payload.kind,
        text=payload.text,
        date=payload.date if payload.date is not None else datetime.now(UTC).date(),
        category=payload.category,
        latitude=payload.latitude,
        longitude=payload.longitude,
        photo_jpeg=payload.photo_jpeg,
        client_id=client_id,
    )
    session.add(observation)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if client_id is None:
            raise
        saved = session.scalar(select(Observation).where(Observation.client_id == client_id))
        if saved is None:
            raise
        # A retry may follow a successful write whose response never reached the device.
        if (
            saved.field_id != payload.field_id
            or saved.kind != payload.kind
            or saved.text != payload.text
            or (payload.date is not None and saved.date != payload.date)
            or saved.category != payload.category
            or saved.latitude != payload.latitude
            or saved.longitude != payload.longitude
            or saved.photo_jpeg != payload.photo_jpeg
        ):
            raise ConflictError(
                "This observation was already saved with different details."
            ) from None
        return saved
    return observation


def observation_photo(session: Session, observation_id: int) -> bytes:
    image = session.scalar(
        select(Observation.photo_jpeg)
        .join(Field, Field.id == Observation.field_id)
        .where(Observation.id == observation_id, Field.farm_id == FARM_ID)
    )
    if image is None:
        raise NotFoundError("Observation photo was not found")
    return image


def recent_scouting(
    session: Session, field_id: str, season: int, today: date
) -> tuple[ScoutingEvidence, ...]:
    reports = session.execute(
        select(Observation.id, Observation.category, Observation.date)
        .join(Field, Field.id == Observation.field_id)
        .where(
            Field.farm_id == FARM_ID,
            Observation.field_id == field_id,
            Observation.category.is_not(None),
            Observation.date >= date(season, 1, 1),
            Observation.date <= min(today, date(season, 12, 31)),
        )
        .order_by(Observation.date.desc(), Observation.id.desc())
        .limit(8)
    )
    return tuple(
        ScoutingEvidence(id=report.id, category=report.category, date=report.date)
        for report in reports
    )
