from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from field_shift.errors import ConflictError, NotFoundError
from field_shift.modules.farm.models import FARM_ID, Farm
from field_shift.modules.farm.service import require_farm
from field_shift.modules.fields.models import Field
from field_shift.modules.fields.service import require_field
from field_shift.modules.rotations.models import FarmRotationDraft, RotationPreference
from field_shift.modules.rotations.planner import optimize
from field_shift.modules.rotations.planning_catalog import CROP_FAMILIES, known_crop
from field_shift.modules.rotations.planning_schemas import (
    CropDecision,
    PlanningContext,
    PlanningField,
    PlanningRead,
    PlanningRequest,
    PlanningSummary,
)


def _field_context(field: Field, start_season: int) -> PlanningField:
    crop = known_crop(field.crop)
    history = (
        [CropDecision(field_id=field.id, season=field.season, crop=crop)]
        if (crop is not None and start_season - 8 <= field.season < start_season)
        else []
    )
    return PlanningField(
        id=field.id,
        name=field.name,
        area_ha=field.area_ha,
        boundary_revision=field.boundary_revision,
        crop=field.crop,
        crop_season=field.season,
        history=history,
    )


def planning_context(session: Session, start_season: int, season_count: int = 3) -> PlanningContext:
    if start_season < require_farm(session).season:
        raise ConflictError("Planning starts in the active farm season or later.")
    fields = session.scalars(select(Field).where(Field.farm_id == FARM_ID).order_by(Field.id))
    return PlanningContext(
        start_season=start_season,
        season_count=season_count,
        fields=[_field_context(field, start_season) for field in fields],
        crops=[{"crop": crop, "family": family} for crop, family in CROP_FAMILIES.items()],
    )


def _snapshot(session: Session, payload: PlanningRequest, *, lock: bool = False) -> dict:
    farm = require_farm(session)
    if payload.start_season < farm.season:
        raise ConflictError("Planning starts in the active farm season or later.")
    fields = []
    preferences = {}
    for field_id in sorted(payload.field_ids):
        field = require_field(session, field_id)
        if lock:
            field = session.scalars(
                select(Field)
                .where(Field.id == field_id, Field.farm_id == FARM_ID)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).one()
        fields.append(_field_context(field, payload.start_season).model_dump(mode="json"))
        preference = session.get(
            RotationPreference, (field_id, payload.start_season - 1), populate_existing=True
        )
        preferences[field_id] = (
            {
                "main_plan_id": preference.main_plan_id,
                "plan_a_crops": preference.plan_a_crops,
                "plan_b_crops": preference.plan_b_crops,
            }
            if preference
            else None
        )
    return {"fields": fields, "preferences": preferences, "farm_season": farm.season}


def _read(draft: FarmRotationDraft) -> PlanningRead:
    return PlanningRead(
        id=draft.id,
        name=draft.name,
        start_season=draft.start_season,
        season_count=draft.request.get("season_count", 3),
        created_at=_timestamp(draft.created_at),
        applied_at=_timestamp(draft.applied_at) if draft.applied_at else None,
        request=draft.request,
        fields=draft.input_snapshot["fields"],
        result=draft.result,
    )


def _timestamp(value: datetime) -> str:
    # SQLite drops timezone metadata; all stored timestamps originate in UTC.
    return value.replace(tzinfo=UTC).isoformat() if value.tzinfo is None else value.isoformat()


def _matching_retry(existing: FarmRotationDraft, payload: PlanningRequest) -> PlanningRead:
    if existing.farm_id != FARM_ID or PlanningRequest.model_validate(existing.request) != payload:
        raise ConflictError("This request identifier is already used by a different plan.")
    return _read(existing)


def create_draft(session: Session, payload: PlanningRequest) -> PlanningRead:
    existing = session.scalar(
        select(FarmRotationDraft).where(FarmRotationDraft.client_id == str(payload.client_id))
    )
    if existing:
        return _matching_retry(existing, payload)
    snapshot = _snapshot(session, payload)
    # Release the read transaction before a bounded CPU search.
    session.rollback()
    result = optimize(
        payload, [PlanningField.model_validate(field) for field in snapshot["fields"]]
    )
    draft = FarmRotationDraft(
        id=str(uuid4()),
        farm_id=FARM_ID,
        client_id=str(payload.client_id),
        name=payload.name,
        start_season=payload.start_season,
        request=payload.model_dump(mode="json"),
        input_snapshot=snapshot,
        result=result.model_dump(mode="json"),
        created_at=datetime.now(UTC),
    )
    session.add(draft)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(FarmRotationDraft).where(FarmRotationDraft.client_id == str(payload.client_id))
        )
        if existing:
            return _matching_retry(existing, payload)
        raise
    return _read(draft)


def list_drafts(session: Session) -> list[PlanningSummary]:
    drafts = session.scalars(
        select(FarmRotationDraft)
        .where(FarmRotationDraft.farm_id == FARM_ID)
        .order_by(FarmRotationDraft.created_at.desc())
        .limit(50)
    )
    return [
        PlanningSummary(
            id=draft.id,
            name=draft.name,
            start_season=draft.start_season,
            season_count=draft.request.get("season_count", 3),
            created_at=_timestamp(draft.created_at),
            applied_at=_timestamp(draft.applied_at) if draft.applied_at else None,
            status=draft.result["status"],
            field_count=len(draft.input_snapshot["fields"]),
        )
        for draft in drafts
    ]


def get_draft(session: Session, draft_id: str) -> PlanningRead:
    draft = session.get(FarmRotationDraft, draft_id)
    if draft is None or draft.farm_id != FARM_ID:
        raise NotFoundError("Rotation plan was not found.")
    return _read(draft)


def apply_draft(session: Session, draft_id: str) -> PlanningRead:
    # Serialize farm-wide apply calls, then lock fields in a stable order. Individual
    # manual plan edits already lock the same field rows before touching preferences.
    session.execute(select(Farm.id).where(Farm.id == FARM_ID).with_for_update()).scalar_one()
    draft = session.scalar(
        select(FarmRotationDraft)
        .where(FarmRotationDraft.id == draft_id, FarmRotationDraft.farm_id == FARM_ID)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if draft is None:
        raise NotFoundError("Rotation plan was not found.")
    if draft.applied_at is not None:
        return _read(draft)
    if draft.result["status"] not in {"optimal", "feasible"}:
        raise ConflictError("Only a feasible rotation plan can be applied.")
    payload = PlanningRequest.model_validate(draft.request)
    current = _snapshot(session, payload, lock=True)
    if current != draft.input_snapshot:
        raise ConflictError("Fields or crop plans changed. Generate a fresh plan before applying.")
    years = list(range(payload.start_season, payload.start_season + payload.season_count))
    by_field = {field_id: {} for field_id in payload.field_ids}
    enabled = {rule.crop for rule in payload.crops}
    for row in draft.result["assignments"]:
        field_id, year, crop = row["field_id"], row["season"], row["crop"]
        if (
            field_id not in by_field
            or year not in years
            or year in by_field[field_id]
            or crop not in enabled
        ):
            raise ConflictError("The rotation plan has invalid field/season assignments.")
        by_field[field_id][year] = crop
    if any(set(rows) != set(years) for rows in by_field.values()):
        raise ConflictError("The rotation plan is incomplete and cannot be applied.")
    for field_id, rows in by_field.items():
        crops = [rows[year] for year in years]
        preference = session.get(RotationPreference, (field_id, payload.start_season - 1))
        if preference is None:
            preference = RotationPreference(
                field_id=field_id, season=payload.start_season - 1, main_plan_id="plan-a"
            )
            session.add(preference)
        preference.plan_a_crops = crops
        preference.main_plan_id = "plan-a"
    draft.applied_at = datetime.now(UTC)
    session.commit()
    return _read(draft)
