from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Crop = Literal["Wheat", "Maize", "Soy", "Barley", "Sunflower", "Rapeseed", "Peas"]
PlannerSeason = Annotated[int, Field(ge=2001, le=2098)]
Hectares = Annotated[float, Field(ge=0, le=3_000_000, allow_inf_nan=False)]


class CropRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    crop: Crop
    min_break_years: Annotated[int, Field(ge=0, le=8)] = 1
    preference: Annotated[int, Field(ge=0, le=10)] = 0
    max_share_percent: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] = 100
    min_area_ha: Hectares = 0
    max_area_ha: Hectares | None = None

    @model_validator(mode="after")
    def area_interval(self) -> "CropRule":
        if self.max_area_ha is not None and self.min_area_ha > self.max_area_ha:
            raise ValueError("Minimum crop area cannot exceed maximum crop area.")
        return self


class CropDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: Annotated[str, Field(min_length=1, max_length=24)]
    season: Annotated[int, Field(ge=1993, le=2100)]
    crop: Crop


class PlanningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: UUID
    name: Annotated[str, Field(min_length=1, max_length=120)] = "Farm rotation plan"
    start_season: PlannerSeason
    field_ids: Annotated[list[str], Field(min_length=1, max_length=30)]
    crops: Annotated[list[CropRule], Field(min_length=2, max_length=7)]
    history: Annotated[list[CropDecision], Field(max_length=240)] = Field(default_factory=list)
    locked: Annotated[list[CropDecision], Field(max_length=90)] = Field(default_factory=list)
    avoid_consecutive_family: bool = False
    require_two_crops: bool = True

    @model_validator(mode="after")
    def consistent_scope(self) -> "PlanningRequest":
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Give the plan a name.")
        if len(set(self.field_ids)) != len(self.field_ids):
            raise ValueError("Select each field once.")
        crop_names = [rule.crop for rule in self.crops]
        if len(set(crop_names)) != len(crop_names):
            raise ValueError("Configure each crop once.")
        for decisions, historical in ((self.history, True), (self.locked, False)):
            keys = [(entry.field_id, entry.season) for entry in decisions]
            if len(set(keys)) != len(keys):
                raise ValueError("Only one crop is allowed per field and season.")
            for entry in decisions:
                if entry.field_id not in self.field_ids:
                    raise ValueError("Crop decisions must belong to selected fields.")
                if historical:
                    if not self.start_season - 8 <= entry.season < self.start_season:
                        raise ValueError("Crop history must be within the eight preceding seasons.")
                elif not self.start_season <= entry.season <= self.start_season + 2:
                    raise ValueError("Locked crops must be within the three planning seasons.")
                elif entry.crop not in crop_names:
                    raise ValueError("A locked crop must be enabled for planning.")
        return self


class PlanningField(BaseModel):
    id: str
    name: str
    area_ha: float
    boundary_revision: int
    crop: str
    crop_season: int
    history: list[CropDecision]


class PlanningContext(BaseModel):
    start_season: int
    fields: list[PlanningField]
    crops: list[dict[str, str]]


class PlannerDiagnostic(BaseModel):
    code: str
    message: str
    field_id: str | None = None
    season: int | None = None
    crop: str | None = None


class PlannedAssignment(BaseModel):
    field_id: str
    season: int
    crop: Crop
    area_ha: float
    locked: bool
    reasons: list[str]


class PlannedArea(BaseModel):
    season: int
    crop: Crop
    area_ha: float
    share_percent: float
    min_area_ha: float
    max_area_ha: float


class PlanningResult(BaseModel):
    status: Literal["optimal", "feasible", "infeasible", "search_limit"]
    assignments: list[PlannedAssignment]
    areas: list[PlannedArea]
    diagnostics: list[PlannerDiagnostic]
    warnings: list[PlannerDiagnostic]
    explored_nodes: int
    node_limit: int
    objective: float | None = None
    objective_unit: Literal["preference_points_ha"] = "preference_points_ha"
    method: Literal["bounded_branch_and_bound_v1"] = "bounded_branch_and_bound_v1"


class PlanningRead(BaseModel):
    id: str
    name: str
    start_season: int
    created_at: str
    applied_at: str | None
    request: PlanningRequest
    fields: list[PlanningField]
    result: PlanningResult


class PlanningSummary(BaseModel):
    id: str
    name: str
    start_season: int
    created_at: str
    applied_at: str | None
    status: str
    field_count: int
