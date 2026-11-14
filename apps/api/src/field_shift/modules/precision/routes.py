from typing import Literal

from fastapi import APIRouter, HTTPException, Response

from field_shift.db import SessionDep
from field_shift.modules.fields.schemas import Season
from field_shift.modules.precision import exports, service
from field_shift.modules.precision.schemas import (
    Approval,
    HarvestImport,
    PrescriptionCreate,
    TrialCreate,
)

router = APIRouter(prefix="/fields/{field_id}/precision", tags=["precision fieldwork"])


def _validated(action):
    try:
        return action()
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@router.get("")
def plans(field_id: str, season: Season, session: SessionDep):
    return service.list_plans(session, field_id, season)


@router.get("/context")
def context(field_id: str, season: Season, session: SessionDep):
    from field_shift.modules.fields.service import require_field
    from field_shift.modules.imagery.vegetation import vegetation_footprint

    field = require_field(session, field_id)
    geometry, fingerprint = vegetation_footprint(session, field, season)
    return {
        "boundary_revision": field.boundary_revision,
        "geometry_fingerprint": fingerprint,
        "geometry": geometry.model_dump(mode="json"),
    }


@router.post("/prescriptions", status_code=201)
def prescription(field_id: str, payload: PrescriptionCreate, session: SessionDep):
    return _validated(lambda: service.create_plan(session, field_id, payload))


@router.post("/trials", status_code=201)
def trial(field_id: str, payload: TrialCreate, session: SessionDep):
    return _validated(lambda: service.create_plan(session, field_id, payload))


@router.get("/{plan_id}")
def detail(field_id: str, plan_id: str, session: SessionDep):
    return service.plan_read(session, service.require_plan(session, field_id, plan_id))


@router.post("/{plan_id}/approve")
def approve(field_id: str, plan_id: str, payload: Approval, session: SessionDep):
    return _validated(lambda: service.approve_plan(session, field_id, plan_id, payload))


@router.post("/{plan_id}/harvest")
def harvest(field_id: str, plan_id: str, payload: HarvestImport, session: SessionDep):
    return _validated(lambda: service.import_harvest(session, field_id, plan_id, payload))


@router.get("/{plan_id}/export/{format_name}")
def export(
    field_id: str,
    plan_id: str,
    format_name: Literal["shapefile", "isoxml", "geojson", "csv"],
    session: SessionDep,
):
    headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
    if format_name == "csv":
        plan = service.require_plan(session, field_id, plan_id)
        data = _validated(lambda: exports.report_csv(plan))
        media_type, suffix = "text/csv", "csv"
    else:
        plan = service.exportable_plan(session, field_id, plan_id)
        if format_name == "geojson":
            import json

            document = {key: value for key, value in plan.snapshot.items() if key != "crs"}
            document["analysis_crs"] = plan.snapshot["crs"]
            data = json.dumps(document, ensure_ascii=False).encode("utf-8")
            media_type, suffix = "application/geo+json", "geojson"
        else:
            render = (
                exports.shapefile_package if format_name == "shapefile" else exports.isoxml_package
            )
            data = _validated(lambda: render(plan))
            media_type, suffix = "application/zip", "zip"
    headers["Content-Disposition"] = (
        f'attachment; filename="FS-{plan.id[:8]}-{format_name}.{suffix}"'
    )
    return Response(data, media_type=media_type, headers=headers)
