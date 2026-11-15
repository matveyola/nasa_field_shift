from datetime import date as CalendarDate
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from field_shift.modules.fields.schemas import Polygon
from field_shift.modules.geo_ml.ssurgo import SoilProfile

SurfaceSource = Literal["demo", "historical", "ml"]
GeoSource = Literal["demo", "historical", "ml", "forecast"]


class MoistureCell(BaseModel):
    geometry: Polygon
    value: float


class MoistureInterval(BaseModel):
    lower: float = Field(ge=0, le=1)
    upper: float = Field(ge=0, le=1)
    coverage: float = Field(gt=0, lt=1)
    empirical_coverage: float | None = Field(default=None, ge=0, le=1)


class MoistureSource(BaseModel):
    name: str
    provider: str
    url: str
    kind: Literal["reanalysis", "ml"]
    resolution_degrees: float
    grid_latitude: float
    grid_longitude: float
    sample_count: int
    requested_latitude: float
    requested_longitude: float
    model_version: str | None = None
    experimental: bool | None = None
    soil_profile: SoilProfile | None = None
    soil_source_url: str | None = None
    interval: MoistureInterval | None = None
    generated_at: datetime | None = None


class GeoResult(BaseModel):
    field_id: str
    season: int
    demo: bool
    date: CalendarDate
    depth_cm: tuple[Literal[0, 5], Literal[5, 7]]
    unit: Literal["m³/m³"]
    minimum: float
    maximum: float
    cells: list[MoistureCell]
    source: MoistureSource | None = None


class MoistureForecastDay(BaseModel):
    date: CalendarDate
    lead_days: int
    value: float
    ifs_value: float
    precipitation_mm: float
    et0_mm: float
    interval: MoistureInterval | None = None


class MoistureForecast(BaseModel):
    product: Literal["moisture_forecast"] = "moisture_forecast"
    field_id: str
    initialized_at: datetime
    fetched_at: datetime
    model_version: str | None = None
    provider: str
    source_url: str
    resolution_km: int
    grid_latitude: float
    grid_longitude: float
    unit: Literal["m³/m³"] = "m³/m³"
    days: list[MoistureForecastDay]
