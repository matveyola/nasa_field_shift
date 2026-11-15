from typing import Annotated, Literal

from field_shift_science.fields.schemas import CropName, Season
from pydantic import BaseModel, Field, model_validator

PlanId = Literal["plan-a", "plan-b"]


class RotationCrop(BaseModel):
    year: int
    crop: str


class RotationPlanSchedule(BaseModel):
    id: PlanId
    name: str
    crops: list[RotationCrop]


class RotationPlan(RotationPlanSchedule):
    stress_days: int
    longest_episode: int
    sensitive_days: int


class RotationRead(BaseModel):
    plans: list[RotationPlan]
    main_plan_id: PlanId
    demo: bool


class MainPlanUpdate(BaseModel):
    field_id: str
    season: Season
    plan_id: PlanId


class PlanUpdate(BaseModel):
    field_id: str
    season: Season
    crops: Annotated[list[CropName], Field(min_length=1, max_length=6)]

    @model_validator(mode="after")
    def horizon(self):
        if self.season + len(self.crops) > 2100:
            raise ValueError("The last crop season must be no later than 2100.")
        return self


class RotationYieldYear(BaseModel):
    year: int
    crop: str
    previous_crop: str | None
    change_t_ha: float | None = None
    lower_t_ha: float | None = None
    upper_t_ha: float | None = None
    reason: str | None = None


class RotationYieldPlan(BaseModel):
    id: PlanId
    estimates: list[RotationYieldYear]


class RotationYieldRead(BaseModel):
    field_id: str
    season: int
    model_version: str | None = None
    status: Literal["validated", "experimental", "unavailable"]
    source_url: str | None = None
    training_sites: int | None = None
    plans: list[RotationYieldPlan]
