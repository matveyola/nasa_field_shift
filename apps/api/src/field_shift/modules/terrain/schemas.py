from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TerrainVector(BaseModel):
    start: tuple[float, float]
    end: tuple[float, float]
    slope_percent: float = Field(ge=0, allow_inf_nan=False)
    elevation_m: float = Field(allow_inf_nan=False)


class TerrainResult(BaseModel):
    analysis: Literal["terrain"] = "terrain"
    field_id: str
    boundary_revision: int
    generated_at: datetime
    status: Literal["ready", "unavailable"]
    reason: str | None = None
    model_version: str
    source: str = "Copernicus DEM GLO-30"
    source_url: str = "https://registry.opendata.aws/copernicus-dem/"
    source_release: str = "2021 AWS release; primary acquisitions 2011–2015"
    source_resolution_m: int = 30
    method: Literal["local_surface_gradient"] = "local_surface_gradient"
    vectors: list[TerrainVector] = Field(default_factory=list)
    elevation_min_m: float | None = Field(default=None, allow_inf_nan=False)
    elevation_max_m: float | None = Field(default=None, allow_inf_nan=False)
    coverage_percent: float = Field(ge=0, le=100, allow_inf_nan=False)
    minimum_display_slope_percent: float
    source_tiles: list[str] = Field(default_factory=list)
    limitations: list[str]
    attribution: str
