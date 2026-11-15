"""Calculate soil indicators from explicitly supplied management and field data."""

from datetime import date
from math import prod
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StringConstraints,
    model_validator,
)

Nonnegative = Annotated[FiniteFloat, Field(ge=0)]
Source = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class LivingCoverPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.end < self.start:
            raise ValueError("Living cover end must be on or after its start.")
        return self


class RusleInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    r: Nonnegative = Field(description="Rainfall erosivity in MJ mm/(ha h year).")
    k: Nonnegative = Field(description="Metric soil erodibility consistent with R.")
    ls: Nonnegative = Field(description="Dimensionless slope length and steepness.")
    c: Nonnegative = Field(description="Dimensionless cover-management factor.")
    p: Nonnegative = Field(description="Dimensionless support-practice factor.")
    source: Source


class HarvestRemovalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    yield_t_ha: Nonnegative = Field(
        description="Harvested tonnes per hectare at the coefficient's moisture basis."
    )
    p2o5_kg_per_t: Nonnegative
    k2o_kg_per_t: Nonnegative
    source: Source


class ErosionManagement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    c: Nonnegative
    p: Nonnegative
    k: Nonnegative | None = None
    ls: Nonnegative | None = None
    slope_percent: Nonnegative | None = None
    slope_length_m: Annotated[FiniteFloat, Field(ge=4.572)] | None = None
    source: Source

    @model_validator(mode="after")
    def complete_uniform_slope(self):
        if (self.slope_percent is None) != (self.slope_length_m is None):
            raise ValueError("Supply slope_percent and slope_length_m together.")
        if self.ls is not None and self.slope_percent is not None:
            raise ValueError("Supply either ls or the uniform slope inputs.")
        return self


class PlanSoilInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    living_cover: list[LivingCoverPeriod] | None = None
    erosion: RusleInputs | None = None
    erosion_management: ErosionManagement | None = None
    harvests: list[HarvestRemovalInput] | None = None

    @model_validator(mode="after")
    def single_erosion_input(self):
        if self.erosion is not None and self.erosion_management is not None:
            raise ValueError("Supply either full erosion factors or management factors.")
        return self


class SoilSummary(BaseModel):
    living_cover_days: int | None = None
    erosion_t_ha_year: FiniteFloat | None = None
    erosion_source: str | None = None
    p2o5_removed_kg_ha: FiniteFloat | None = None
    k2o_removed_kg_ha: FiniteFloat | None = None
    harvest_removal_sources: list[str] | None = None


def _cover_days(periods: list[LivingCoverPeriod], window_start: date, window_end: date) -> int:
    clipped = sorted(
        (max(period.start, window_start), min(period.end, window_end))
        for period in periods
        if period.start <= window_end and period.end >= window_start
    )
    days = 0
    last_end = None
    for start, end in clipped:
        if last_end is None or start > last_end:
            days += (end - start).days + 1
        elif end > last_end:
            days += (end - last_end).days
        last_end = max(last_end, end) if last_end is not None else end
    return days


def summarize_soil(
    inputs: PlanSoilInputs | None, window_start: date, window_end: date
) -> SoilSummary:
    """Summarize inclusive cover dates, RUSLE screening and harvested nutrients."""
    if window_end < window_start:
        raise ValueError("Soil assessment window ends before it starts.")
    if inputs is None:
        return SoilSummary()

    cover = inputs.living_cover
    erosion = inputs.erosion
    harvests = inputs.harvests
    return SoilSummary(
        living_cover_days=(
            _cover_days(cover, window_start, window_end) if cover is not None else None
        ),
        erosion_t_ha_year=(
            round(prod((erosion.r, erosion.k, erosion.ls, erosion.c, erosion.p)), 3)
            if erosion is not None
            else None
        ),
        erosion_source=erosion.source if erosion is not None else None,
        p2o5_removed_kg_ha=(
            round(sum(row.yield_t_ha * row.p2o5_kg_per_t for row in harvests), 3)
            if harvests is not None
            else None
        ),
        k2o_removed_kg_ha=(
            round(sum(row.yield_t_ha * row.k2o_kg_per_t for row in harvests), 3)
            if harvests is not None
            else None
        ),
        harvest_removal_sources=(
            list(dict.fromkeys(row.source for row in harvests)) if harvests is not None else None
        ),
    )
