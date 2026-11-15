"""Assemble trusted field context and persist verified completed decision chains."""

from datetime import UTC, date, datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from field_shift.jobs.models import Job, JobStatus
from field_shift.modules.fields.planning_profile.service import ProfileError, ProfileRepository

from . import service
from .schemas import HandoffSubmission
from .strategy_models import StrategyScenario
from .strategy_schemas import (
    MAX_CALENDAR_YEARS,
    HistoryEntry,
    SavedChoice,
    SavedStrategy,
    StrategyContext,
)

_CROP_IDS = {
    "corn": "maize_grain",
    "canola": "rapeseed_canola",
    "maize": "maize_grain",
    "oat": "oats",
    "pea": "peas_dry",
    "peas": "peas_dry",
    "rapeseed": "rapeseed_canola",
    "soy": "soybeans",
    "soybean": "soybeans",
    "spring_wheat": "wheat_spring",
    "wheat": "wheat",
    "wheat_winter": "wheat",
    "winter_wheat": "wheat",
}
_UNKNOWN_CROPS = {"", "unknown", "unspecified"}
_SNAPSHOT_METRICS = (
    "choice_id",
    "crop_id",
    "status",
    "rank",
    "water",
    "economics",
    "agronomy",
    "erosion",
    "scenarios",
    "warnings",
)


def crop_identity(value):
    crop = value.strip().casefold().replace(" ", "_")
    return None if crop in _UNKNOWN_CROPS else _CROP_IDS.get(crop, crop)


def field_history(session, field):
    try:
        profile = ProfileRepository(session).latest(field.farm_id, field.id)
    except ProfileError as error:
        if error.code not in {"not_found", "stale"}:
            raise
        profile = None
    entries = (
        {
            row.season: HistoryEntry(
                season_year=row.season,
                crop_id=crop_identity(row.crop.crop_id) if row.crop else None,
                sowing_date=row.sowing_date,
                harvest_date=row.harvest_date,
            )
            for row in profile.history
        }
        if profile
        else {}
    )
    registered = crop_identity(field.crop)
    latest = entries.get(field.season)
    if registered and (latest is None or latest.crop_id is None):
        if latest is None:
            latest = HistoryEntry(season_year=field.season, crop_id=registered, sowing_date=None)
        entries[field.season] = latest.model_copy(update={"crop_id": registered})
    return sorted(entries.values(), key=lambda row: row.season_year, reverse=True)[:12]


def context(session, principal, field_id):
    field = service.scoped_field(session, principal, field_id)
    return StrategyContext(
        field_id=field.id,
        geometry_revision=field.boundary_revision,
        area_ha=field.area_ha,
        history=field_history(session, field),
    )


def observed_history(session, field, body, first_planting_date):
    history = field_history(session, field)
    if any(row.season_year >= first_planting_date.year for row in history):
        raise HTTPException(422, "The scenario must follow the latest recorded crop season")
    crop = crop_identity(body.history_crop_id) if body.history_crop_id else None
    sowing = body.history_sowing_date
    if crop is not None or sowing is not None:
        previous = history[0] if history else None
        if (crop and previous and previous.crop_id) or (
            sowing and previous and previous.sowing_date
        ):
            raise HTTPException(422, "Fill only missing history; known crop records stay unchanged")
        if previous is None and (crop is None or sowing is None):
            raise HTTPException(422, "Provide both crop and sowing date to add missing history")
        if sowing and (sowing >= first_planting_date or sowing > datetime.now(UTC).date()):
            raise HTTPException(422, "Factual sowing must be in the past and before the scenario")
        if previous is None:
            history.append(HistoryEntry(season_year=sowing.year, crop_id=crop, sowing_date=sowing))
        else:
            if sowing and (
                sowing.year not in {previous.season_year - 1, previous.season_year}
                or (previous.harvest_date and sowing > previous.harvest_date)
            ):
                raise HTTPException(422, "The sowing date does not fit the previous crop season")
            history[0] = previous.model_copy(
                update={
                    "crop_id": crop or previous.crop_id,
                    "sowing_date": sowing or previous.sowing_date,
                }
            )
    return [
        {
            "season_year": row.season_year,
            "land_use": "crop" if row.crop_id else "unknown",
            "crop_id": row.crop_id,
            "planting_date": row.sowing_date.isoformat()
            if row.crop_id and row.sowing_date
            else None,
            "harvest_date": row.harvest_date.isoformat()
            if row.crop_id and row.harvest_date
            else None,
        }
        for row in reversed(history)
    ]


def require_editor(principal):
    if principal.role not in {"admin", "editor"}:
        raise HTTPException(403, "Editing permission is required")


def current_field(session, principal, field_id, revision):
    field = service.scoped_field(session, principal, field_id, lock=True)
    if field.boundary_revision != revision:
        raise HTTPException(409, "Field boundary changed; reload the field")
    return field


def job_choice(session, field, decision):
    job = session.get(Job, decision.job_id)
    if (
        job is None
        or job.field_id != field.id
        or job.inputs.get("farm_id") != field.farm_id
        or job.analysis != "rotation_handoff"
        or job.inputs.get("operation") != "climate_step"
        or job.inputs.get("strategy", {}).get("mode") != "sequence"
    ):
        raise HTTPException(404, "Strategy step not found")
    if job.boundary_revision != field.boundary_revision or job.status != JobStatus.SUCCEEDED:
        raise HTTPException(409, "A current completed step is required")
    result = (job.result or {}).get("envelope", {})
    payload = result.get("payload", {})
    options = set(payload.get("ranking", {}).get("selection_ids", []))
    candidate = next(
        (row for row in payload.get("candidates", []) if row.get("choice_id") == decision.crop_id),
        None,
    )
    if (
        result.get("status") != "available"
        or decision.crop_id not in options
        or candidate is None
        or candidate.get("choice", {}).get("crop_id") != decision.crop_id
    ):
        raise HTTPException(422, "Choose an eligible crop returned by this step")
    return job, candidate


def decision_chain(session, field, decisions):
    prefix = []
    records = []
    for decision in decisions:
        job, candidate = job_choice(session, field, decision)
        strategy = job.inputs["strategy"]
        if strategy["previous"] != prefix or (
            records and strategy["settings"] != records[0][0].inputs["strategy"]["settings"]
        ):
            raise HTTPException(409, "The selected steps do not form the same scenario")
        records.append((job, candidate))
        prefix.append(decision.model_dump(mode="json"))
    return records


def submit(session, principal, field_id, body):
    require_editor(principal)
    field = current_field(session, principal, field_id, body.geometry_revision)
    records = decision_chain(session, field, body.previous)
    selected = [
        candidate["choice"] | {"planting_date": job.inputs["parameters"]["planting_date"]}
        for job, candidate in records
    ]
    sowing = body.planting_date.isoformat()
    if selected and int(selected[-1]["planting_date"][:4]) >= body.planting_date.year:
        raise HTTPException(422, "The model supports one sowing per year in ascending order")
    first_sowing = (
        date.fromisoformat(selected[0]["planting_date"]) if selected else body.planting_date
    )
    if body.planting_date.year - first_sowing.year >= MAX_CALENDAR_YEARS:
        raise HTTPException(422, "A sequence can span at most ten calendar years")
    history = observed_history(session, field, body, first_sowing)
    irrigation = body.irrigation.model_dump(mode="json")
    settings = {
        "step_count": body.step_count,
        "priority": body.priority,
        "irrigation": irrigation,
        "history": history,
    }
    if records and settings != records[0][0].inputs["strategy"]["settings"]:
        raise HTTPException(409, "Input changes require recalculation from the first step")
    volume = body.irrigation.daily_m3
    if volume is not None and field.area_ha <= 0:
        raise HTTPException(422, "A positive field area is required for irrigation conversion")
    daily_mm = volume / (field.area_ha * 10) if volume is not None else None
    parameters = {
        "strategy_mode": body.mode,
        "planting_date": sowing,
        "selected": selected,
        "observed_crop_history": history,
        "farmer": {"priorities": [body.priority]},
        "irrigation": {
            "status": body.irrigation.status,
            "daily_mm": daily_mm,
            "method": body.irrigation.method,
        },
    }
    if body.comparison_crops is not None:
        parameters["candidates"] = [{"crop_id": crop} for crop in body.comparison_crops]
    return service.submit(
        session,
        principal,
        field_id,
        HandoffSubmission(
            request_id=body.request_id,
            geometry_revision=body.geometry_revision,
            operation="climate_step",
            parameters=parameters,
            allow_public_data=True,
        ),
        strategy={
            "mode": body.mode,
            "settings": settings,
            "previous": [decision.model_dump(mode="json") for decision in body.previous],
        },
    )


def list_saved(session, principal, field_id, *, offset=0, limit=20):
    field = service.scoped_field(session, principal, field_id)
    rows = session.scalars(
        select(StrategyScenario)
        .where(
            StrategyScenario.field_id == field.id,
            StrategyScenario.geometry_revision == field.boundary_revision,
        )
        .order_by(StrategyScenario.created_at.desc(), StrategyScenario.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return [SavedStrategy.model_validate(row.document) for row in rows]


def save(session, principal, field_id, body):
    require_editor(principal)
    field = current_field(session, principal, field_id, body.geometry_revision)
    records = decision_chain(session, field, body.choices)
    settings = records[0][0].inputs["strategy"]["settings"]
    if len(records) != settings["step_count"]:
        raise HTTPException(422, "Complete all steps before saving a scenario")
    fingerprint = service.digest([choice.model_dump(mode="json") for choice in body.choices])
    existing = select(StrategyScenario).where(
        StrategyScenario.field_id == field.id,
        StrategyScenario.geometry_revision == field.boundary_revision,
        StrategyScenario.fingerprint == fingerprint,
    )
    if row := session.scalar(existing):
        return SavedStrategy.model_validate(row.document)
    assumptions = {}
    for job, _ in records:
        envelope = job.result["envelope"]
        payload = envelope["payload"]
        for note in [
            payload.get("weather_dataset"),
            payload.get("assumptions", {}).get("irrigation"),
        ]:
            if note:
                assumptions[note] = None
        assumptions.update(dict.fromkeys(envelope.get("limitations", [])))
    saved = SavedStrategy(
        id=str(uuid4()),
        field_id=field.id,
        geometry_revision=field.boundary_revision,
        created_at=datetime.now(UTC),
        priority=settings["priority"],
        irrigation=settings["irrigation"],
        choices=[
            SavedChoice(
                job_id=job.id,
                crop_id=decision.crop_id,
                planting_date=job.inputs["parameters"]["planting_date"],
                metrics={key: candidate[key] for key in _SNAPSHOT_METRICS if key in candidate},
            )
            for decision, (job, candidate) in zip(body.choices, records, strict=True)
        ],
        assumptions=list(assumptions),
    )
    session.add(
        StrategyScenario(
            id=saved.id,
            field_id=saved.field_id,
            geometry_revision=saved.geometry_revision,
            fingerprint=fingerprint,
            created_at=saved.created_at,
            document=saved.model_dump(mode="json"),
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        row = session.scalar(existing)
        if row is None:
            raise
        return SavedStrategy.model_validate(row.document)
    return saved
