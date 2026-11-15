"""Durable monthly vegetation snapshots, assembled without network work in HTTP reads."""

import calendar
import logging
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from json import dumps
from time import monotonic

from field_shift_science.fields.schemas import Polygon
from field_shift_science.weather.scenarios import Scenario
from sqlalchemy import select
from sqlalchemy.orm import Session, defer, sessionmaker

from field_shift.jobs import repository
from field_shift.jobs.models import Job, JobKind, JobStatus
from field_shift.jobs.schemas import JobInputs
from field_shift.modules.fields.history import boundary_for_season
from field_shift.modules.fields.models import Field, FieldPlanting
from field_shift.modules.fields.service import require_field
from field_shift.modules.imagery.vegetation_catalog import (
    VegetationUnavailableError,
    search_month,
)
from field_shift.modules.imagery.vegetation_raster import analyse_scene
from field_shift.modules.imagery.vegetation_schemas import (
    METHOD_VERSION,
    SPECTRAL_VERSION,
    VegetationCell,
    VegetationMonth,
    VegetationMonthResult,
    VegetationSceneResult,
    VegetationSeason,
)

logger = logging.getLogger(__name__)
ACTIVE = (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYING)
REFRESH_AFTER = timedelta(hours=24)
PUBLICATION_GRACE = timedelta(days=7)


def vegetation_footprint(session: Session, field: Field, season: int) -> tuple[Polygon, str]:
    planting = session.get(FieldPlanting, (field.id, season))
    boundary, _, _ = boundary_for_season(session, field, season)
    geometry = Polygon.model_validate(planting.geometry if planting else boundary)
    signature = dumps(geometry.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return geometry, sha256(signature.encode()).hexdigest()


def _aware(at: datetime) -> datetime:
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)


def season_months(season: int, today: date) -> range:
    if not 2017 <= season <= today.year:
        raise ValueError("Choose a Sentinel-2 season between 2017 and the current year.")
    return range(1, today.month + 1 if season == today.year else 13)


def run_vegetation_month(
    field_id: str,
    season: int,
    cutoff: date,
    geometry: Polygon,
    geometry_fingerprint: str,
    submitted_at: datetime,
    boundary_revision: int,
) -> VegetationMonthResult:
    at = _aware(submitted_at)
    season_months(season, at.date())
    if cutoff.year != season or cutoff > at.date():
        raise ValueError(
            "The vegetation cutoff must be in the selected season and not in the future."
        )
    result = VegetationMonthResult(
        field_id=field_id,
        season=season,
        month=cutoff.month,
        cutoff=cutoff,
        geometry_fingerprint=geometry_fingerprint,
        boundary_revision=boundary_revision,
        generated_at=datetime.now(UTC),
        status="no_clear_scene",
        spectral_version=SPECTRAL_VERSION,
    )
    # Refuse oversized/global windows before querying external services.
    x, y = zip(*geometry.coordinates[0], strict=True)
    if max(y) > 84 or min(y) < -80 or max(x) - min(x) > 0.2 or max(y) - min(y) > 0.2:
        result.status = "unsupported"
        result.reason = "This footprint is outside the supported bounded Sentinel-2 analysis area."
        return result
    deadline = monotonic() + 75
    candidates = search_month(geometry, cutoff.replace(day=1), cutoff)
    for candidate in candidates:
        if monotonic() > deadline:
            # Never persist a timeout as a cloud-free-data absence; the durable job retries.
            raise VegetationUnavailableError(VegetationUnavailableError.public_message)
        result.candidates_checked += 1
        try:
            analysed = analyse_scene(candidate, geometry)
        except ValueError as error:
            result.status = "unsupported"
            result.reason = str(error)
            return result
        if analysed is not None:
            result.status = "ready"
            result.observation, result.heterogeneity = analysed
            return result
    result.reason = (
        "No verified, covering Sentinel-2 scene was available among the bounded catalog results."
        if not candidates
        else "No examined scene had at least 70% valid coverage and ten valid 20 m pixels."
    )
    return result


def _jobs(
    session: Session,
    field: Field,
    season: int,
    fingerprint: str,
    month: int | None = None,
) -> list[Job]:
    month_filter = (
        Job.inputs["sample_date"].as_string().like(f"{season}-{month:02d}-%")
        if month is not None
        else True
    )
    return list(
        session.scalars(
            select(Job)
            .options(defer(Job.result))
            .where(
                *_job_scope(field, season, fingerprint),
                month_filter,
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(512)
        )
    )


def _job_scope(field: Field, season: int, fingerprint: str):
    return (
        Job.kind == JobKind.GEO_ML,
        Job.field_id == field.id,
        Job.inputs["analysis"].as_string() == "vegetation",
        Job.inputs["season"].as_integer() == season,
        Job.inputs["vegetation_mask_hash"].as_string() == fingerprint,
        Job.matches_boundary_revision(field.boundary_revision),
    )


def vegetation_scene(
    session: Session, field_id: str, season: int, observation_date: date, scene_id: str
) -> VegetationSceneResult | None:
    today = datetime.now(UTC).date()
    season_months(season, today)
    if observation_date.year != season or observation_date > today:
        raise ValueError("Choose an observation date in the selected season, up to today.")
    field = require_field(session, field_id)
    _, fingerprint = vegetation_footprint(session, field, season)
    # Use the acquisition identity, never the monthly job cutoff or latest snapshot.
    # A new scene may supersede the selected observation while a request is in flight.
    day = observation_date.isoformat()
    payload = session.scalar(
        select(Job.result)
        .where(
            *_job_scope(field, season, fingerprint),
            Job.status == JobStatus.SUCCEEDED,
            Job.result["method_version"].as_string() == METHOD_VERSION,
            Job.result["observation"]["date"].as_string() == day,
            Job.result["observation"]["scene_id"].as_string() == scene_id,
            Job.result["heterogeneity"]["date"].as_string() == day,
        )
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )
    if payload is None:
        return None
    return VegetationSceneResult.model_validate(payload)


def _month(job: Job) -> int | None:
    try:
        return date.fromisoformat(job.inputs["sample_date"]).month
    except (KeyError, ValueError, TypeError):
        return None


def vegetation_season(
    session: Session,
    field_id: str,
    season: int,
    *,
    now: datetime | None = None,
    include_cells: bool = True,
) -> VegetationSeason:
    today = _aware(now or datetime.now(UTC)).date()
    months = season_months(season, today)
    field = require_field(session, field_id)
    _, fingerprint = vegetation_footprint(session, field, season)
    boundary_geometry, _, boundary_history_assumed = boundary_for_season(session, field, season)
    result = VegetationSeason(
        field_id=field.id,
        season=season,
        boundary_revision=field.boundary_revision,
        geometry_fingerprint=fingerprint,
        boundary_geometry=Polygon.model_validate(boundary_geometry),
        boundary_history_assumed=boundary_history_assumed,
        status="unavailable",
    )
    jobs = _jobs(session, field, season, fingerprint)
    latest: dict[int, Job] = {}
    snapshot_ids: dict[int, str] = {}
    for job in jobs:
        month = _month(job)
        if month is None:
            continue
        latest.setdefault(month, job)
        if job.status == JobStatus.SUCCEEDED:
            snapshot_ids.setdefault(month, job.id)
    snapshots = _snapshot_summaries(session, snapshot_ids)
    map_job_id = None
    for month in months:
        job = latest.get(month)
        snapshot = snapshots.get(month)
        row = VegetationMonth(month=month, status="missing", job_id=job.id if job else None)
        if snapshot:
            row.status, row.reason = snapshot.status, snapshot.reason
            if result.generated_at is None or snapshot.generated_at > result.generated_at:
                result.generated_at = snapshot.generated_at
            if snapshot.observation:
                result.observations.append(snapshot.observation)
            if snapshot.heterogeneity and (
                result.heterogeneity is None
                or snapshot.heterogeneity.date > result.heterogeneity.date
            ):
                result.heterogeneity = snapshot.heterogeneity
                map_job_id = snapshot_ids[month]
        if job and job.status in ACTIVE:
            row.status, row.reason = "pending", None
        elif job and job.status == JobStatus.FAILED:
            row.status, row.reason = "failed", job.error
        result.months.append(row)
    result.observations.sort(key=lambda observation: observation.date)
    if result.observations:
        result.status = (
            "ready" if all(row.status == "ready" for row in result.months) else "partial"
        )
    if include_cells and result.heterogeneity and map_job_id:
        cells = (
            session.scalar(select(Job.result["heterogeneity"]["cells"]).where(Job.id == map_job_id))
            or []
        )
        result.heterogeneity.cells = [VegetationCell.model_validate(cell) for cell in cells]
    return result


def _snapshot_summaries(session: Session, identifiers: dict[int, str]):
    """Project small metadata in SQL; historical map grids never enter API memory."""
    if not identifiers:
        return {}
    keys = (
        "field_id",
        "season",
        "month",
        "boundary_revision",
        "geometry_fingerprint",
        "generated_at",
        "cutoff",
        "status",
        "reason",
        "observation",
        "candidates_checked",
        "resolution_m",
        "source_url",
        "method_version",
    )
    spatial = (
        "date",
        "median_ndvi",
        "spread_ndvi",
        "low_area_percent",
        "valid_percent",
        "low_threshold",
        "high_threshold",
        "resolution_m",
    )
    statement = select(
        *[Job.result[key].label(key) for key in keys],
        *[Job.result["heterogeneity"][key].label("map_" + key) for key in spatial],
    ).where(
        Job.id.in_(identifiers.values()), Job.result["method_version"].as_string() == METHOD_VERSION
    )
    snapshots = {}
    for row in session.execute(statement).mappings():
        data = {key: row[key] for key in keys}
        if row["map_date"] is not None:
            data["heterogeneity"] = {key: row["map_" + key] for key in spatial} | {"cells": []}
        snapshots[data["month"]] = VegetationMonthResult.model_validate(data)
    return snapshots


def queue_vegetation_refresh(
    session: Session,
    field_id: str,
    season: int,
    *,
    now: datetime | None = None,
    max_new_jobs: int = 12,
    retry_failed: bool = False,
    only_month: int | None = None,
) -> list[Job]:
    at = _aware(now or datetime.now(UTC))
    months = season_months(season, at.date())
    if only_month is not None:
        months = [month for month in months if month == only_month]
    field = require_field(session, field_id)
    _, fingerprint = vegetation_footprint(session, field, season)
    latest: dict[int, Job] = {}
    for job in _jobs(session, field, season, fingerprint, only_month):
        if (month := _month(job)) is not None:
            latest.setdefault(month, job)
    versions = (
        dict(
            session.execute(
                select(Job.id, Job.result["method_version"].as_string()).where(
                    Job.id.in_([job.id for job in latest.values()])
                )
            ).all()
        )
        if latest
        else {}
    )
    # Only an explicit user refresh upgrades closed snapshots with new bands.
    # Background maintenance does not suddenly redownload a farm's whole archive.
    spectral_versions = (
        dict(
            session.execute(
                select(Job.id, Job.result["spectral_version"].as_string()).where(
                    Job.id.in_([job.id for job in latest.values()])
                )
            ).all()
        )
        if latest and retry_failed
        else {}
    )
    queued = []
    created = 0
    for month in reversed(months):
        cutoff = min(date(season, month, calendar.monthrange(season, month)[1]), at.date())
        previous = latest.get(month)
        outdated_method = False
        if previous is not None:
            if previous.status in ACTIVE:
                queued.append(previous)
                continue
            if previous.status == JobStatus.SUCCEEDED:
                version_matches = versions.get(previous.id) == METHOD_VERSION
                spectral_upgrade = (
                    retry_failed and spectral_versions.get(previous.id) != SPECTRAL_VERSION
                )
                outdated_method = not version_matches or spectral_upgrade
                if (
                    version_matches
                    and not spectral_upgrade
                    and cutoff < at.date().replace(day=1)
                    and previous.inputs.get("sample_date") == cutoff.isoformat()
                ):
                    # Catch up a partial month at rollover, then make one final lookup
                    # after the publication grace period. Only then freeze the cache.
                    final_day = cutoff + PUBLICATION_GRACE
                    if _aware(previous.created_at).date() >= final_day or at.date() < final_day:
                        continue
            if (
                not outdated_method
                and not (retry_failed and previous.status == JobStatus.FAILED)
                and _aware(previous.updated_at) + REFRESH_AFTER > at
            ):
                continue
        if created >= max_new_jobs:
            break
        inputs = JobInputs(
            season=season,
            scenario=Scenario.BASELINE,
            source="historical",
            analysis="vegetation",
            sample_date=cutoff,
            vegetation_mask_hash=fingerprint,
        ).stored()
        signature = dumps(
            [
                METHOD_VERSION,
                SPECTRAL_VERSION,
                field.id,
                field.boundary_revision,
                fingerprint,
                season,
                month,
                cutoff.isoformat(),
                at.date().isoformat(),
                previous.id
                if previous and (previous.status == JobStatus.FAILED or outdated_method)
                else None,
            ]
        )
        key = (
            repository.INTERNAL_KEY_PREFIX + "vegetation:" + sha256(signature.encode()).hexdigest()
        )
        queued.append(
            repository.submit(
                session,
                kind=JobKind.GEO_ML,
                field_id=field.id,
                inputs=inputs,
                boundary_revision=field.boundary_revision,
                idempotency_key=key,
                now=at,
            )
        )
        created += 1
    return queued


def enqueue_vegetation_refresh(
    factory: sessionmaker[Session], *, now: datetime | None = None
) -> int:
    """Refresh known seasons only; each invocation queues at most 24 new jobs."""
    at = _aware(now or datetime.now(UTC))
    after_id = ""
    count = 0
    while count < 24:
        with factory.begin() as session:
            fields = list(
                session.scalars(
                    select(Field).where(Field.id > after_id).order_by(Field.id).limit(100)
                )
            )
            if not fields:
                break
            after_id = fields[-1].id
            for field in fields:
                # Do not download years for every newly imported field without an initial
                # user request. Once requested, the active season updates automatically.
                subscribed = set(
                    session.scalars(
                        select(Job.inputs["season"].as_integer())
                        .where(
                            Job.field_id == field.id,
                            Job.inputs["analysis"].as_string() == "vegetation",
                            Job.inputs["season"].as_integer().in_((at.year, at.year - 1)),
                        )
                        .distinct()
                    )
                )
                if not subscribed:
                    continue
                for season in (at.year, at.year - 1):
                    if season not in subscribed or count >= 24:
                        continue
                    # Complete December after the calendar changes, including a service
                    # outage over New Year. No other archived months are rescanned.
                    jobs = queue_vegetation_refresh(
                        session,
                        field.id,
                        season,
                        now=at,
                        max_new_jobs=24 - count,
                        only_month=12 if season < at.year else None,
                    )
                    count += sum(_aware(job.created_at) == at for job in jobs)
                if count >= 24:
                    break
    return count
