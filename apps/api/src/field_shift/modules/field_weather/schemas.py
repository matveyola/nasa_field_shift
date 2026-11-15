from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(Record):
    provider: str
    url: str
    retrieved_at: datetime
    description: str


class Scope(Record):
    field_id: str
    boundary_revision: int
    geometry_fingerprint: str
    generated_at: datetime
    latitude: float
    longitude: float
    sources: list[Source]


class Hour(Record):
    time: datetime
    temperature_c: float | None
    rain_mm: float | None
    gust_ms: float | None
    below_zero: bool | None


class ForecastDay(Record):
    date: date
    rain_mm: float | None
    rain_probability_percent: float | None


class WeatherSummary(Scope):
    timezone: str
    hours: list[Hour]
    hours_complete: bool
    forecast_days: list[ForecastDay]
    requested_start: date
    requested_end: date
    missing_dates: list[date]
    forecast_rain_mm: float | None
    wettest_dates: list[date]
    lowest_probability_dates: list[date]


class TypicalPeriod(Record):
    start: date
    end: date
    rain_mm: float


class RainPeriod(Record):
    length_days: int
    dry_days: int | None
    start: date
    end: date
    complete: bool
    missing_dates: list[date]
    rain_mm: float | None
    typical_rain_mm: float | None
    difference_mm: float | None
    typical_periods: list[TypicalPeriod]
    typical_status: Literal["not_requested", "available", "unavailable"]


class RainHistory(Scope):
    timezone: Literal["UTC"] = "UTC"
    requested_end: date
    available_end: date | None
    archive_lag_days: int | None
    periods: list[RainPeriod]
    limitations: list[str]
