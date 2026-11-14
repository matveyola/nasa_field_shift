from datetime import UTC, datetime
from hashlib import sha256
from json import dumps

from shapely.ops import unary_union
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from field_shift.errors import ConflictError, NotFoundError
from field_shift.modules.fields.models import Field
from field_shift.modules.fields.service import require_field
from field_shift.modules.imagery.vegetation import vegetation_footprint, vegetation_scene
from field_shift.modules.precision.geometry import feature, metric_crs, polygonal, project
from field_shift.modules.precision.models import PrecisionPlan
from field_shift.modules.precision.schemas import (
    UNITS,
    Approval,
    HarvestImport,
    PrecisionCreate,
    PrescriptionCreate,
    TrialCreate,
)
from field_shift.modules.precision.trials import design_trial, harvest_report, parse_harvest


def _hash(value) -> str:
    return sha256(dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _lock_field(session: Session, field_id: str) -> Field:
    require_field(session, field_id)
    return session.scalar(
        select(Field)
        .where(Field.id == field_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _footprint(session: Session, field: Field, season: int, revision: int, fingerprint: str):
    geometry, current_hash = vegetation_footprint(session, field, season)
    if field.boundary_revision != revision or current_hash != fingerprint:
        raise ConflictError("The field boundary or planted area changed. Create a fresh map.")
    return geometry.model_dump(mode="json")


def require_plan(session: Session, field_id: str, plan_id: str, *, lock=False) -> PrecisionPlan:
    require_field(session, field_id)
    statement = select(PrecisionPlan).where(
        PrecisionPlan.id == plan_id, PrecisionPlan.field_id == field_id
    )
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    plan = session.scalar(statement)
    if plan is None:
        raise NotFoundError("The application map was not found.")
    return plan


def plan_read(session: Session, plan: PrecisionPlan, *, detail=True) -> dict:
    field = require_field(session, plan.field_id)
    _, fingerprint = vegetation_footprint(session, field, plan.season)
    result = {
        "id": plan.id,
        "field_id": plan.field_id,
        "season": plan.season,
        "kind": plan.kind,
        "name": plan.name,
        "version": plan.version,
        "status": plan.status,
        "created_at": plan.created_at.replace(tzinfo=UTC).isoformat(),
        "approved_at": plan.approved_at.replace(tzinfo=UTC).isoformat()
        if plan.approved_at
        else None,
        "stale": field.boundary_revision != plan.boundary_revision
        or fingerprint != plan.geometry_fingerprint,
        "operation": plan.snapshot["operation"],
        "product": plan.snapshot["product"],
        "unit": plan.snapshot["unit"],
        "area_ha": plan.snapshot["area_ha"],
        "has_harvest": plan.harvest is not None,
    }
    if detail:
        result.update(snapshot=plan.snapshot, harvest=plan.harvest, harvest_name=plan.harvest_name)
    return result


def list_plans(session: Session, field_id: str, season: int) -> list[dict]:
    require_field(session, field_id)
    plans = session.scalars(
        select(PrecisionPlan)
        .where(PrecisionPlan.field_id == field_id, PrecisionPlan.season == season)
        .order_by(PrecisionPlan.created_at.desc())
        .limit(100)
    )
    return [plan_read(session, plan, detail=False) for plan in plans]


def create_plan(session: Session, field_id: str, payload: PrecisionCreate) -> dict:
    field = _lock_field(session, field_id)
    kind = "trial" if isinstance(payload, TrialCreate) else "prescription"
    request_hash = _hash({"field_id": field_id, "kind": kind, **payload.model_dump(mode="json")})
    prior = session.get(PrecisionPlan, str(payload.client_id))
    if prior is not None:
        if prior.field_id != field_id or prior.request_hash != request_hash:
            raise ConflictError("This request identifier was already used for another map.")
        return plan_read(session, prior)
    geometry = _footprint(
        session, field, payload.season, payload.boundary_revision, payload.geometry_fingerprint
    )
    crs = metric_crs(geometry)
    field_shape = project(geometry, crs)
    if isinstance(payload, TrialCreate):
        features = design_trial(geometry, crs, payload)
        provenance = {
            "method": "randomized_complete_blocks",
            "design": payload.model_dump(mode="json", exclude={"client_id"}),
        }
    elif isinstance(payload, PrescriptionCreate):
        scene = vegetation_scene(
            session, field_id, payload.season, payload.observation_date, payload.scene_id
        )
        if not scene or not scene.heterogeneity.cells:
            raise ConflictError("The selected processed scene is no longer available.")
        if len(scene.heterogeneity.cells) > 5000:
            raise ValueError("The selected scene exceeds the 5,000-cell precision map limit.")
        grouped = {}
        for cell in scene.heterogeneity.cells:
            if cell.zone not in payload.rates:
                raise ValueError("Enter a rate for every mapped zone.")
            clipped = polygonal(
                project(cell.geometry.model_dump(mode="json"), crs).intersection(field_shape)
            )
            if not clipped.is_empty:
                grouped.setdefault(cell.zone, []).append(clipped)
        features, covered = [], []
        for zone, items in grouped.items():
            geometry_zone = unary_union(items)
            if covered and geometry_zone.intersection(unary_union(covered)).area > 0.01:
                raise ValueError("The source zones overlap; rebuild the scene before exporting.")
            covered.append(geometry_zone)
            features.append(feature(geometry_zone, crs, zone=zone, rate=payload.rates[zone]))
        if not features:
            raise ValueError("No mapped zones intersect the planted area.")
        remainder = polygonal(field_shape.difference(unary_union(covered)))
        if not remainder.is_empty and remainder.area > 0.0001:
            features.append(feature(remainder, crs, zone="fallback", rate=payload.fallback_rate))
        provenance = {
            "method": "farmer_rates_on_sentinel2_ndvi_zones",
            "scene_id": scene.observation.scene_id,
            "observation_date": scene.observation.date.isoformat(),
            "valid_percent": scene.observation.valid_percent,
            "resolution_m": scene.heterogeneity.resolution_m,
        }
    else:
        raise ValueError("Unsupported precision plan type.")
    snapshot = {
        "type": "FeatureCollection",
        "features": features,
        "footprint": geometry,
        "field_name": field.name,
        "boundary_revision": field.boundary_revision,
        "geometry_fingerprint": payload.geometry_fingerprint,
        "crs": crs,
        "operation": payload.operation,
        "product": payload.product,
        "unit": UNITS[payload.operation],
        "fallback_rate": payload.fallback_rate,
        "area_ha": field_shape.area / 10000,
        "provenance": provenance,
    }
    plan = PrecisionPlan(
        id=str(payload.client_id),
        field_id=field_id,
        season=payload.season,
        kind=kind,
        name=payload.name,
        request_hash=request_hash,
        boundary_revision=field.boundary_revision,
        geometry_fingerprint=payload.geometry_fingerprint,
        version=1,
        status="draft",
        snapshot=snapshot,
        created_at=datetime.now(UTC),
    )
    session.add(plan)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        prior = session.get(PrecisionPlan, str(payload.client_id))
        if prior is None:
            raise
        if prior.field_id != field_id or prior.request_hash != request_hash:
            raise ConflictError(
                "This request identifier was already used for another map."
            ) from None
        return plan_read(session, prior)
    return plan_read(session, plan)


def approve_plan(session: Session, field_id: str, plan_id: str, payload: Approval) -> dict:
    field = _lock_field(session, field_id)
    plan = require_plan(session, field_id, plan_id, lock=True)
    _footprint(session, field, plan.season, plan.boundary_revision, plan.geometry_fingerprint)
    if plan.status == "approved":
        # The geometry/rates snapshot is immutable. A lost response can safely be replayed.
        return plan_read(session, plan)
    if plan.version != payload.version:
        raise ConflictError("The map changed. Reload it before confirming.")
    if any(item["properties"]["rate"] == 0 for item in plan.snapshot["features"]):
        if not payload.confirmed_zero_rates:
            raise ValueError("Confirm that zero rates intentionally turn application off.")
    if plan.status != "approved":
        plan.status = "approved"
        plan.approved_at = datetime.now(UTC)
        plan.version += 1
    session.commit()
    return plan_read(session, plan)


def exportable_plan(session: Session, field_id: str, plan_id: str) -> PrecisionPlan:
    field = _lock_field(session, field_id)
    plan = require_plan(session, field_id, plan_id)
    _footprint(session, field, plan.season, plan.boundary_revision, plan.geometry_fingerprint)
    if plan.status != "approved":
        raise ConflictError("Confirm the rates and map before machinery export.")
    return plan


def import_harvest(session: Session, field_id: str, plan_id: str, payload: HarvestImport) -> dict:
    plan = require_plan(session, field_id, plan_id, lock=True)
    if plan.kind != "trial":
        raise ValueError("Harvest comparisons require a saved field trial.")
    if plan.status != "approved":
        raise ConflictError("Confirm the trial design before attaching harvest measurements.")
    content_hash = sha256(payload.csv.encode()).hexdigest()
    if plan.harvest_hash == content_hash:
        return plan_read(session, plan)
    if plan.version != payload.version:
        raise ConflictError("The trial changed. Reload before importing measurements.")
    rows = parse_harvest(payload.csv)
    plan.harvest = harvest_report(plan.snapshot["features"], rows, plan.snapshot["crs"])
    plan.harvest["imported_at"] = datetime.now(UTC).isoformat()
    plan.harvest["samples"] = rows
    plan.harvest_hash = content_hash
    plan.harvest_name = payload.file_name
    plan.version += 1
    session.commit()
    return plan_read(session, plan)
