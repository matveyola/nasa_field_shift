from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from field_shift.modules.fields.geometry import area_hectares
from field_shift.modules.fields.schemas import MAX_AREA_HA, FieldRead, Polygon, Season
from field_shift.modules.rotations.planning_schemas import Crop, PlannerSeason


class CropHistory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    season: Season
    crop: Crop | None


class SetupInputs(BaseModel):
    """Full draft replacement; omitted answers remain distinct from explicit unknowns."""

    model_config = ConfigDict(extra="forbid")

    country_code: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
    region_code: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
        | None
    ) = None
    geometry: Polygon | None = None
    name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
        | None
    ) = None
    history: Annotated[list[CropHistory], Field(max_length=8)] | None = None
    allowed_crops: Annotated[list[Crop], Field(max_length=7)] | None = None
    irrigation: bool | None = None
    priority: Literal["water", "soil", "low_change"] | None = None
    start_season: PlannerSeason | None = None

    @model_validator(mode="after")
    def valid_inputs(self) -> "SetupInputs":
        if self.geometry and not 0 < area_hectares(self.geometry.coordinates[0]) <= MAX_AREA_HA:
            raise ValueError("The field must be greater than 0 and at most 100,000 hectares.")
        years = [entry.season for entry in self.history or []]
        if len(years) != len(set(years)):
            raise ValueError("Provide at most one crop for each historical season.")
        if self.allowed_crops and len(self.allowed_crops) != len(set(self.allowed_crops)):
            raise ValueError("Select each crop once.")
        if self.start_season and any(year >= self.start_season for year in years):
            raise ValueError("Crop history must precede the first planning season.")
        return self


class DemoCrop(BaseModel):
    year: int
    crop: Crop


class DemoRotationPlan(BaseModel):
    demo: Literal[True] = True
    source: Literal["onboarding_demo_v1"] = "onboarding_demo_v1"
    start_season: int
    crops: list[DemoCrop]
    alternatives: Annotated[
        list[Annotated[list[DemoCrop], Field(min_length=3, max_length=3)]], Field(max_length=2)
    ] = Field(default_factory=list)
    explanation: str
    limitations: list[str]


class SetupRead(BaseModel):
    id: str
    status: Literal["draft", "completed"]
    # Preserve omitted questionnaire fields rather than serializing them as answered nulls.
    inputs: dict
    field: FieldRead | None
    demo_plan: DemoRotationPlan | None
