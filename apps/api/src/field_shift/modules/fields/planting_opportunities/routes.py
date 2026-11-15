from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from field_shift.db import SessionDep
from field_shift.modules.access.dependencies import require_principal

from .schemas import Opportunities
from .service import opportunities

router = APIRouter(prefix="/fields", tags=["planting-opportunities"])


@router.get("/{field_id}/planting-opportunities", response_model=Opportunities)
def read_opportunities(
    field_id: str,
    request: Request,
    session: SessionDep,
    planning_date: date | None = None,
    year_count: Annotated[int, Query(ge=1, le=6)] = 1,
):
    principal = require_principal(request)
    now = datetime.now(UTC)
    planning_date = planning_date or now.date()
    if not 1900 <= planning_date.year or planning_date.year + year_count > 2100:
        raise HTTPException(422, "Choose a planning date and one to six years ending by 2100.")
    return opportunities(session, principal, field_id, planning_date, year_count, now=now)
