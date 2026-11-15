from datetime import UTC, datetime
from sqlite3 import SQLITE_BUSY, SQLITE_LOCKED
from uuid import UUID

from field_shift_science.fields.geometry import area_hectares
from field_shift_science.fields.schemas import FieldCreate
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from field_shift.errors import ConflictError, NotFoundError
from field_shift.modules.farm.models import FARM_ID, Farm
from field_shift.modules.fields.service import create_field, field_read, require_field
from field_shift.modules.onboarding.demo_plan import build_demo_plan
from field_shift.modules.onboarding.models import FieldSetup
from field_shift.modules.onboarding.schemas import (
    DemoRotationPlan,
    SetupInputs,
    SetupRead,
)


def _lock_farm(session: Session) -> Farm:
    # Serialize setup writes, including the absent-row case, and field number allocation.
    if session.get_bind().dialect.name == "sqlite":
        # SQLite ignores FOR UPDATE. Acquire its write lock before reading the setup,
        # avoiding competing read-to-write upgrades during a double click or safe retry.
        try:
            session.execute(
                update(Farm)
                .where(Farm.id == FARM_ID)
                .values(next_field_number=Farm.next_field_number)
            )
        except OperationalError as error:
            if getattr(error.orig, "sqlite_errorcode", 0) & 255 in (SQLITE_BUSY, SQLITE_LOCKED):
                raise ConflictError(
                    "Another field update is in progress. Try again shortly."
                ) from error
            raise
    farm = session.scalar(select(Farm).where(Farm.id == FARM_ID).with_for_update())
    if farm is None:
        raise NotFoundError("Farm was not found.")
    return farm


def _require_setup(session: Session, setup_id: UUID) -> FieldSetup:
    setup = session.get(FieldSetup, str(setup_id), populate_existing=True)
    if setup is None or setup.farm_id != FARM_ID:
        raise NotFoundError("Field setup was not found.")
    return setup


def _read(session: Session, setup: FieldSetup) -> SetupRead:
    field = require_field(session, setup.field_id) if setup.field_id else None
    return SetupRead(
        id=setup.id,
        status="completed" if setup.completed_at else "draft",
        inputs=setup.inputs,
        field=field_read(field, irrigation_available=setup.inputs.get("irrigation"))
        if field is not None
        else None,
        demo_plan=setup.demo_plan,
    )


def get_setup(session: Session, setup_id: UUID) -> SetupRead:
    return _read(session, _require_setup(session, setup_id))


def get_field_setup(session: Session, field_id: str) -> SetupRead:
    require_field(session, field_id)
    setup = session.scalar(
        select(FieldSetup).where(FieldSetup.field_id == field_id, FieldSetup.farm_id == FARM_ID)
    )
    if setup is None:
        raise NotFoundError("This field has no saved onboarding profile.")
    return _read(session, setup)


def save_setup(session: Session, setup_id: UUID, payload: SetupInputs) -> SetupRead:
    farm = _lock_farm(session)
    setup = session.get(FieldSetup, str(setup_id), populate_existing=True)
    if setup and setup.farm_id != FARM_ID:
        raise NotFoundError("Field setup was not found.")
    inputs = payload.model_dump(mode="json", exclude_unset=True)
    inputs["start_season"] = (
        payload.start_season
        or (setup.inputs.get("start_season") if setup else None)
        or min(farm.season + 1, 2100)
    )
    if setup and setup.completed_at:
        if setup.inputs != inputs:
            raise ConflictError("This setup is complete. Start another setup to add a field.")
        return _read(session, setup)
    if not farm.season <= inputs["start_season"] <= 2100:
        raise ConflictError(
            "Choose a planning year in the active farm year or later, through 2100."
        )
    if any(entry.season > farm.season for entry in payload.history or []):
        raise ConflictError("Actual crop history cannot include a future farm season.")
    now = datetime.now(UTC)
    if setup is None:
        setup = FieldSetup(
            id=str(setup_id), farm_id=FARM_ID, inputs=inputs, created_at=now, updated_at=now
        )
        session.add(setup)
    else:
        setup.inputs = inputs
        setup.updated_at = now
    session.commit()
    return _read(session, setup)


def _demo_plan(inputs: SetupInputs) -> DemoRotationPlan:
    latest = max(inputs.history, key=lambda entry: entry.season, default=None)
    return build_demo_plan(
        inputs.allowed_crops, inputs.priority, inputs.start_season, latest.crop if latest else None
    )


def complete_setup(session: Session, setup_id: UUID) -> SetupRead:
    farm = _lock_farm(session)
    setup = _require_setup(session, setup_id)
    if setup.completed_at:
        return _read(session, setup)
    inputs = SetupInputs.model_validate(setup.inputs)
    if (
        inputs.geometry is None
        or inputs.history is None
        or "irrigation" not in setup.inputs
        or inputs.priority is None
        or len(inputs.allowed_crops or []) < 1
    ):
        raise ConflictError("Confirm a boundary and answer the field questions before finishing.")
    if inputs.start_season is None or inputs.start_season < farm.season:
        raise ConflictError("The farm season changed. Update the setup before finishing.")
    latest = max(inputs.history, key=lambda entry: entry.season, default=None)
    # 'Unknown' is explicit missing information, never a fabricated real crop observation.
    crop = latest.crop if latest and latest.season == farm.season and latest.crop else "Unknown"
    field = create_field(
        session,
        FieldCreate(
            name=inputs.name or f"Field {farm.next_field_number}",
            crop=crop,
            season=farm.season,
            geometry=inputs.geometry,
            area_ha=area_hectares(inputs.geometry.coordinates[0]),
        ),
        commit=False,
    )
    setup.field_id = field.id
    # Existing multi-crop examples remain explicitly illustrative. Field creation
    # does not depend on being able to produce a three-year demonstration.
    setup.demo_plan = (
        _demo_plan(inputs).model_dump(mode="json")
        if len(inputs.allowed_crops) >= 2 and inputs.start_season <= 2098
        else None
    )
    setup.completed_at = setup.updated_at = datetime.now(UTC)
    session.commit()
    return _read(session, setup)
