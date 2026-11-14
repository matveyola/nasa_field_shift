from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from field_shift.modules.fields.boundary_suggestion import BoundarySuggestion, suggest_boundary

Longitude = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
Latitude = Annotated[float, Field(ge=-80, le=84, allow_inf_nan=False)]


class BoundaryClick(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: tuple[Longitude, Latitude]


router = APIRouter(prefix="/fields", tags=["field boundary suggestion"])


@router.post("/boundary-suggestion", response_model=BoundarySuggestion | None)
def boundary_suggestion(click: BoundaryClick) -> BoundarySuggestion | None:
    return suggest_boundary(*click.position)
