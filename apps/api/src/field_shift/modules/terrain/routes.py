from fastapi import APIRouter

from field_shift.db import SessionDep
from field_shift.jobs.models import Job
from field_shift.jobs.schemas import JobResponse
from field_shift.modules.terrain.schemas import TerrainResult
from field_shift.modules.terrain.service import latest_terrain, submit_terrain

router = APIRouter(prefix="/fields", tags=["terrain"])


@router.get("/{field_id}/terrain", response_model=TerrainResult | None)
def field_terrain(field_id: str, session: SessionDep) -> TerrainResult | None:
    return latest_terrain(session, field_id)


@router.post("/{field_id}/terrain/refresh", response_model=JobResponse, status_code=202)
def refresh_field_terrain(field_id: str, session: SessionDep) -> Job:
    job = submit_terrain(session, field_id)
    session.commit()
    return job
