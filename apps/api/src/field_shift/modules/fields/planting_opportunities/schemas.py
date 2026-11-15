"""Calendar reference opportunities are not model predictions or sowing instructions."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Period(Record):
    start: date
    end: date


class Source(Record):
    kind: Literal["reviewed_window", "regional_typical_month"]
    version: str
    source_url: str
    license: str | None = None
    checksum_sha256: str | None = None
    available_at: datetime
    reference_year: int | None = None
    resolution_arc_minutes: float | None = None
    grid_latitude: float | None = None
    grid_longitude: float | None = None


class Opportunity(Record):
    id: str
    crop_id: str
    cycle_id: str | None
    system: str
    calendar_kind: Literal["reviewed_window", "regional_typical_month", "unavailable"]
    date_precision: Literal["day", "month", "unknown"]
    calendar_status: Literal["open", "current_reference_month", "upcoming", "passed", "unknown"]
    planting_period: Period | None
    maturity_period: Period | None
    maturity_basis: Literal["reviewed_growth_window_end", "regional_maturity_month", "unknown"]
    harvest_year: int | None
    next_reference_date: date | None
    eligibility: Literal["review_required", "not_assessed", "ineligible", "unavailable"]
    missing_data: list[str]
    reasons: list[str]
    source: Source | None
    profit: None = None
    profit_status: Literal["unassessed"] = "unassessed"
    allocation_supported: bool = True
    native_crop_profile_available: bool
    model_evaluation_status: Literal["not_run"] = "not_run"


class Opportunities(Record):
    field_id: str
    geometry_revision: int
    profile_revision: int | None
    profile_status: str
    planning_date: date
    generated_at: datetime
    knowledge_as_of: datetime
    year_count: int
    lookahead_end: date
    horizon_basis: Literal["rolling_calendar_years"] = "rolling_calendar_years"
    allocation_horizon_max_years: Literal[6] = 6
    model_request_horizon_max_years: Literal[3] = 3
    ranking: Literal["calendar_chronology_not_profit"] = "calendar_chronology_not_profit"
    items: list[Opportunity] = Field(max_length=256)
    limitations: list[str]
