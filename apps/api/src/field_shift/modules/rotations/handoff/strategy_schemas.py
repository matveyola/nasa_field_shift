"""Field-scoped single-crop decisions and voluntary scenario snapshots."""

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from .schemas import Input

MIN_YEAR = 2022
MAX_YEAR = 2049
MIN_MONTH = 3
MAX_MONTH = 7
MAX_STEPS = 6
MAX_CALENDAR_YEARS = 10

CropId = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]
Priority = Literal[
    "water_deficit",
    "drought_stress",
    "water_stress",
    "water_saving",
    "yield",
    "revenue",
    "revenue_after_seed",
    "soil_cover",
]


class HistoryEntry(Input):
    season_year: int
    crop_id: str | None
    sowing_date: date | None
    harvest_date: date | None = None


class StrategyContext(Input):
    field_id: str
    geometry_revision: int
    area_ha: float
    min_year: int = MIN_YEAR
    max_year: int = MAX_YEAR
    min_month: int = MIN_MONTH
    max_month: int = MAX_MONTH
    max_steps: int = MAX_STEPS
    max_calendar_years: int = MAX_CALENDAR_YEARS
    history: list[HistoryEntry]


class Irrigation(Input):
    status: Literal["unknown", "none", "available"] = "unknown"
    daily_m3: float | None = Field(default=None, gt=0, le=1_000_000, allow_inf_nan=False)
    method: str | None = Field(default=None, min_length=1, max_length=40)

    @model_validator(mode="after")
    def availability(self):
        if self.status != "available" and (self.daily_m3 is not None or self.method is not None):
            raise ValueError("Only available irrigation can specify a volume or method")
        return self


class Decision(Input):
    job_id: str = Field(min_length=1, max_length=36)
    crop_id: CropId


class StrategyRun(Input):
    request_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    geometry_revision: int = Field(ge=0)
    mode: Literal["sequence", "compare"]
    planting_date: date
    step_count: int = Field(ge=1, le=MAX_STEPS)
    priority: Priority
    previous: list[Decision] = Field(default_factory=list, max_length=MAX_STEPS - 1)
    comparison_crops: tuple[CropId, CropId] | None = None
    history_crop_id: CropId | None = None
    history_sowing_date: date | None = None
    irrigation: Irrigation = Field(default_factory=Irrigation)

    @model_validator(mode="after")
    def check_timeline(self):
        planting = self.planting_date
        if not (MIN_YEAR <= planting.year <= MAX_YEAR and MIN_MONTH <= planting.month <= MAX_MONTH):
            raise ValueError("Choose a sowing date from March through July, 2022–2049")
        if self.mode == "compare":
            if (
                self.step_count != 1
                or self.previous
                or self.comparison_crops is None
                or len(set(self.comparison_crops)) != 2
            ):
                raise ValueError("Compare exactly two distinct crops on one sowing date")
        elif self.comparison_crops is not None or len(self.previous) >= self.step_count:
            raise ValueError("A sequence predicts only its next unselected step")
        if planting.year + self.step_count - len(self.previous) - 1 > MAX_YEAR:
            raise ValueError("The remaining steps exceed the supported climate horizon")
        return self


class SaveStrategy(Input):
    geometry_revision: int = Field(ge=0)
    choices: list[Decision] = Field(min_length=1, max_length=MAX_STEPS)


class SavedChoice(Decision):
    planting_date: date
    metrics: dict[str, Any]


class SavedStrategy(Input):
    id: str
    field_id: str
    geometry_revision: int
    created_at: datetime
    priority: Priority
    irrigation: Irrigation
    choices: list[SavedChoice]
    assumptions: list[str]
