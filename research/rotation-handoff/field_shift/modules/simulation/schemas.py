from datetime import date as CalendarDate
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

from field_shift.modules.weather.scenarios import Scenario


class WaterSource(StrEnum):
    DEMO = "demo"
    HISTORICAL = "historical"


class SourceReference(BaseModel):
    label: str
    url: str


class HistoricalProvenance(BaseModel):
    weather_source: str
    weather_model: str
    weather_url: str
    latitude: float
    longitude: float
    grid_latitude: float
    grid_longitude: float
    grid_resolution_degrees: float
    timezone: str
    start_date: CalendarDate
    end_date: CalendarDate
    rainfall_multiplier: float
    initial_reserve_mm: float
    initial_reserve_source: Literal["era5_prior_day", "user_input"]
    initial_moisture_date: CalendarDate | None
    initial_theta_m3_m3: float | None
    capacity_mm: float
    root_depth_m: float
    field_capacity: float
    wilting_point: float
    crop_coefficient: float
    depletion_fraction: float
    model_version: str
    limitations: list[str]
    sources: list[SourceReference]


class WaterDay(BaseModel):
    date: CalendarDate
    reserve_mm: float
    opening_mm: float
    rain_mm: float
    irrigation_mm: float
    water_use_mm: float
    drainage_mm: float
    source_rain_mm: float | None = None
    reference_et0_mm: float | None = None


class WaterResult(BaseModel):
    field_id: str
    season: int
    scenario: Scenario
    demo: bool
    source: WaterSource = WaterSource.DEMO
    provenance: HistoricalProvenance | None = None
    threshold_mm: float
    flowering_start: CalendarDate | None
    flowering_end: CalendarDate | None
    first_stress_date: CalendarDate | None
    min_reserve_mm: float
    stress_days: int
    days: list[WaterDay]


class WaterForecastDay(WaterDay):
    lead_days: int
    root_zone_estimate_mm: float | None = None
    crop_coefficient: float | None = None
    in_flowering_window: bool | None = None


class WaterForecast(BaseModel):
    analysis: Literal["water_forecast"] = "water_forecast"
    field_id: str
    season: int
    crop: str
    initialized_at: datetime
    generated_at: datetime
    method: Literal["fao56", "ml"]
    model_version: str | None = None
    physical_model_version: str
    provider: str
    source_url: str
    grid_resolution_km: int
    grid_latitude: float
    grid_longitude: float
    root_depth_m: float
    initial_reserve_mm: float
    capacity_mm: float
    threshold_mm: float
    field_capacity: float
    wilting_point: float
    crop_coefficient: float
    soil_source: Literal["reference_loam", "soilgrids_marsden"] = "reference_loam"
    planting_date: CalendarDate | None = None
    flowering_start: CalendarDate | None = None
    flowering_end: CalendarDate | None = None
    phenology_source_url: str | None = None
    limitations: list[str]
    days: list[WaterForecastDay]
