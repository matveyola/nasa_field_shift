from datetime import date, datetime
from typing import Literal

from field_shift_science.fields.schemas import Polygon
from pydantic import BaseModel, ConfigDict, Field

from field_shift.jobs.schemas import JobResponse

METHOD_VERSION = "sentinel-ndvi-monthly-v1"
SOURCE_URL = "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a"
SPECTRAL_VERSION = "sentinel-spectral-b08-v1"
SpectralIndex = Literal["ndre", "reci", "mcari", "ndmi", "ndwi"]


class SpectralSummary(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    mean: float
    p10: float
    p90: float
    valid_percent: float
    pixel_count: int


class VegetationObservation(BaseModel):
    date: date
    acquired_at: datetime
    scene_id: str
    mean_ndvi: float
    p10_ndvi: float
    p90_ndvi: float
    valid_percent: float
    pixel_count: int
    catalog_url: str
    mean_msavi: float | None = None
    indices: dict[SpectralIndex, SpectralSummary] = Field(default_factory=dict)


class VegetationCell(BaseModel):
    geometry: Polygon
    value: float
    zone: Literal["low", "typical", "high"]
    msavi: float | None = None
    indices: dict[SpectralIndex, float] = Field(default_factory=dict)


class Heterogeneity(BaseModel):
    date: date
    median_ndvi: float
    spread_ndvi: float
    low_area_percent: float
    valid_percent: float
    low_threshold: float
    high_threshold: float
    resolution_m: int
    cells: list[VegetationCell]


class VegetationMonthResult(BaseModel):
    analysis: Literal["vegetation"] = "vegetation"
    method_version: str = METHOD_VERSION
    field_id: str
    season: int
    month: int
    boundary_revision: int
    geometry_fingerprint: str
    generated_at: datetime
    cutoff: date
    status: Literal["ready", "no_clear_scene", "unsupported"]
    reason: str | None = None
    observation: VegetationObservation | None = None
    heterogeneity: Heterogeneity | None = None
    candidates_checked: int = 0
    resolution_m: int = 20
    source_url: str = SOURCE_URL
    spectral_version: str | None = None


class VegetationSceneResult(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    field_id: str
    season: int
    boundary_revision: int
    geometry_fingerprint: str
    observation: VegetationObservation
    heterogeneity: Heterogeneity


class VegetationMonth(BaseModel):
    month: int
    status: Literal["ready", "no_clear_scene", "unsupported", "pending", "failed", "missing"]
    reason: str | None = None
    job_id: str | None = None


class VegetationSeason(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    field_id: str
    season: int
    boundary_revision: int
    geometry_fingerprint: str
    boundary_geometry: Polygon | None = None
    boundary_history_assumed: bool = False
    generated_at: datetime | None = None
    status: Literal["ready", "partial", "unavailable"]
    sampling: Literal["monthly_clear_scene"] = "monthly_clear_scene"
    resolution_m: int = 20
    observations: list[VegetationObservation] = Field(default_factory=list)
    heterogeneity: Heterogeneity | None = None
    months: list[VegetationMonth] = Field(default_factory=list)
    source_url: str = SOURCE_URL
    limitations: list[str] = Field(
        default_factory=lambda: [
            "One representative clear observation per calendar month; not a daily series.",
            "Clouds, shadows, snow and uncertain pixels are excluded with the Sentinel-2 SCL mask.",
            "NDVI uses 10 m reflectance aggregated to the 20 m cloud-mask grid.",
            "Large display grids are coarsened; partly masked display blocks are omitted.",
            "Coverage and low-area share use pixel centres inside the analysed footprint; "
            "low-area share is relative to valid observed pixels, not the entire field.",
            "Low and high zones differ from the field median by 0.15 NDVI; "
            "they indicate relative vegetation variation, not a diagnosis or yield loss.",
            "Up to three low-cloud scenes are examined per month; no clear scene means "
            "no suitable observation among those bounded candidates.",
        ]
    )


class VegetationRefresh(BaseModel):
    jobs: list[JobResponse]
