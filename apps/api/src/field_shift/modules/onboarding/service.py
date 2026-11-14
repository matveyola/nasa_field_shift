from datetime import UTC, datetime
from itertools import chain, permutations, product
from sqlite3 import SQLITE_BUSY, SQLITE_LOCKED
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from field_shift.errors import ConflictError, NotFoundError
from field_shift.modules.farm.models import FARM_ID, Farm
from field_shift.modules.fields.geometry import area_hectares
from field_shift.modules.fields.schemas import FieldCreate, FieldRead
from field_shift.modules.fields.service import create_field, require_field
from field_shift.modules.onboarding.models import FieldSetup
from field_shift.modules.onboarding.schemas import (
    DemoCrop,
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
        field=FieldRead.model_validate(field) if field else None,
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
        or farm.season + 1
    )
    if setup and setup.completed_at:
        if setup.inputs != inputs:
            raise ConflictError("This setup is complete. Start another setup to add a field.")
        return _read(session, setup)
    if not farm.season < inputs["start_season"] <= 2098:
        raise ConflictError("Choose a planning season after the active farm season, through 2098.")
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
    # An illustrative ordering of the farmer's crops, not an agronomic model or forecast.
    ordering = {
        "water": ("Wheat", "Barley", "Peas", "Soy", "Rapeseed", "Sunflower", "Maize"),
        "soil": ("Soy", "Peas", "Wheat", "Barley", "Rapeseed", "Sunflower", "Maize"),
        "low_change": tuple(inputs.allowed_crops or []),
    }[inputs.priority]
    allowed = [crop for crop in ordering if crop in inputs.allowed_crops]
    latest = max(inputs.history, key=lambda entry: entry.season, default=None)
    preceding = latest.crop if latest else None
    previous = preceding
    crops = []
    for offset in range(3):
        # Cycle through selected crops; avoid repeating the immediately preceding crop.
        choices = allowed[offset % len(allowed) :] + allowed[: offset % len(allowed)]
        chosen = next(crop for crop in choices if crop != previous)
        crops.append(DemoCrop(year=inputs.start_season + offset, crop=chosen))
        previous = chosen
    alternatives = []
    seen = {tuple(crop.crop for crop in crops)}
    # Prefer permutations containing three different selected crops, then alternating
    # sequences (including the two-crop case). The seven-crop catalogue bounds this search.
    for sequence in chain(permutations(allowed, 3), product(allowed, repeat=3)):
        if (
            sequence in seen
            or sequence[0] == preceding
            or any(a == b for a, b in zip(sequence, sequence[1:]))
        ):
            continue
        alternatives.append(
            [
                DemoCrop(year=inputs.start_season + offset, crop=crop)
                for offset, crop in enumerate(sequence)
            ]
        )
        seen.add(sequence)
        if len(alternatives) == 2:
            break
    return DemoRotationPlan(
        start_season=inputs.start_season,
        crops=crops,
        alternatives=alternatives,
        explanation=(
            "Illustrative three-season sequence using your selected crops and priority. "
            "It avoids an immediate repeat of the last reported crop."
        ),
        limitations=[
            "Demo plan: no yield, water-risk or soil-health prediction was calculated.",
            "Local suitability and agronomic return intervals have not been validated.",
            "Unknown crop history remains unknown; review this example before farm decisions.",
        ],
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
        or len(inputs.allowed_crops or []) < 2
    ):
        raise ConflictError("Confirm a boundary and answer the field questions before finishing.")
    if inputs.start_season is None or inputs.start_season <= farm.season:
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
    setup.demo_plan = _demo_plan(inputs).model_dump(mode="json")
    setup.completed_at = setup.updated_at = datetime.now(UTC)
    session.commit()
    return _read(session, setup)
