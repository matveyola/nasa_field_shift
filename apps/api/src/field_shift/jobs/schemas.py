from datetime import UTC, date, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from field_shift.jobs.models import BOUNDARY_REVISION_INPUT, JobKind, JobStatus
from field_shift.modules.fields.schemas import Season
from field_shift.modules.geo_ml.schemas import GeoSource
from field_shift.modules.weather.scenarios import Scenario

Analysis = Literal[
    "water_balance", "rotation_comparison", "water_forecast", "vegetation", "terrain"
]


class JobInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    season: Season
    scenario: Scenario = Scenario.DRY_YEAR
    irrigation_mm: float = Field(default=0, ge=0, le=500, allow_inf_nan=False)
    source: GeoSource = "demo"
    analysis: Analysis = "water_balance"
    start_date: date | None = None
    end_date: date | None = None
    initial_reserve_mm: float | None = Field(default=None, ge=0, le=2000, allow_inf_nan=False)
    flowering_start: date | None = None
    flowering_end: date | None = None
    sample_date: date | None = None
    latest: bool = False
    vegetation_mask_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        for start, end in (
            (self.start_date, self.end_date),
            (self.flowering_start, self.flowering_end),
        ):
            if (start is None) != (end is None):
                raise ValueError("Supply both dates in a range.")
            if start is not None and end is not None and start > end:
                raise ValueError("A date range must start on or before its end.")
        return self

    def stored(self) -> dict[str, Any]:
        inputs = self.model_dump(mode="json", exclude_none=True)
        if self.source == "demo":
            # Keep previously persisted demo submissions comparable by idempotency key.
            del inputs["source"]
        if self.analysis == "water_balance":
            del inputs["analysis"]
        if not self.latest:
            del inputs["latest"]
        return inputs

    @classmethod
    def from_stored(cls, inputs: dict[str, Any]) -> Self:
        return cls.model_validate(
            {key: value for key, value in inputs.items() if key != BOUNDARY_REVISION_INPUT}
        )


class JobSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: JobKind
    field_id: str = Field(min_length=1, max_length=64)
    inputs: JobInputs

    @model_validator(mode="after")
    def validate_options(self) -> Self:
        options = self.inputs
        water_options = any(
            value is not None
            for value in (options.start_date, options.initial_reserve_mm, options.flowering_start)
        )
        if options.analysis in ("vegetation", "terrain"):
            if (
                self.kind != JobKind.GEO_ML
                or options.source != "historical"
                or options.scenario != Scenario.BASELINE
                or options.irrigation_mm != 0
                or water_options
                or options.latest
                or options.end_date is not None
                or options.flowering_end is not None
            ):
                raise ValueError("Geospatial analysis requires baseline geo_ml historical inputs.")
            if options.analysis == "terrain" and (
                options.sample_date is not None or options.vegetation_mask_hash is not None
            ):
                raise ValueError("Terrain analysis does not accept vegetation dates or masks.")
            if options.analysis == "vegetation" and (
                options.sample_date is None
                or options.sample_date.year != options.season
                or options.sample_date > datetime.now(UTC).date()
                or options.season < 2017
            ):
                raise ValueError(
                    "Choose a vegetation month in the selected season, from 2017 to today."
                )
            return self
        if options.vegetation_mask_hash is not None:
            raise ValueError("Vegetation masks apply only to vegetation analysis.")
        if options.source == "demo" and (water_options or options.sample_date is not None):
            raise ValueError("Date and initialization overrides require historical data.")
        if self.kind == JobKind.GEO_ML and water_options:
            raise ValueError("Water balance options do not apply to a moisture job.")
        if self.kind == JobKind.SIMULATION and options.sample_date is not None:
            raise ValueError("Use a date range for a water balance job.")
        if options.analysis == "water_forecast":
            if (
                self.kind != JobKind.SIMULATION
                or options.source != "forecast"
                or options.scenario != Scenario.BASELINE
                or options.irrigation_mm != 0
                or water_options
            ):
                raise ValueError("Water forecasts require baseline inputs and the forecast source.")
        elif self.kind != JobKind.GEO_ML and options.source in ("ml", "forecast"):
            raise ValueError("The moisture model requires a geo_ml job.")
        if options.source == "forecast" and options.sample_date is not None:
            raise ValueError("A live forecast uses the newest complete ECMWF initialization.")
        if options.latest and (
            self.kind != JobKind.GEO_ML
            or options.source != "historical"
            or options.sample_date is not None
        ):
            raise ValueError("Use latest only for an undated ERA5-Land moisture job.")
        if (
            options.source == "ml"
            and options.sample_date is not None
            and options.sample_date.year != options.season
        ):
            raise ValueError("The moisture date must be within the selected season.")
        if options.analysis == "rotation_comparison" and (
            self.kind != JobKind.SIMULATION
            or options.source != "historical"
            or options.initial_reserve_mm is not None
            or options.flowering_start is not None
        ):
            raise ValueError(
                "Rotation comparison requires historical simulation data and crop-specific inputs."
            )
        return self


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: JobKind
    analysis: Analysis | None
    field_id: str
    boundary_revision: int
    status: JobStatus
    attempts: int
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def attach_utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
