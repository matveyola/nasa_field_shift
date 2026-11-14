"""Explicit input snapshots; upstream datasets and acquisition stay outside inference."""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Era5Day:
    day: date
    available_at: datetime
    source_sha256: str
    surface_vwc: float | None  # ERA5-Land 0–7 cm, m³/m³
    subsurface_vwc: float | None  # ERA5-Land 7–28 cm, m³/m³
    soil_temperature_c: float | None
    air_temperature_c: float | None
    snow_depth_m: float | None


@dataclass(frozen=True)
class SmapDay:
    day: date
    available_at: datetime
    source_sha256: str
    surface_vwc: float | None
    retrieval_quality_flag: int | None  # Required; bit 0 clear means recommended retrieval.


@dataclass(frozen=True)
class SoilFeatures:
    available_at: datetime
    source_sha256: str
    sand_percent: float
    clay_percent: float
    organic_matter_percent: float
    sample_depth_cm: float = 5.0
    source: str = "USDA_SSURGO"


@dataclass(frozen=True)
class HistoricalRequest:
    estimated_on: date
    as_of: datetime
    latitude: float
    longitude: float
    region_code: str
    era5: tuple[Era5Day, ...]
    smap: tuple[SmapDay, ...] = ()
    soil: SoilFeatures | None = None


@dataclass(frozen=True)
class SurfaceResult:
    status: str
    reason: str
    mode: str | None
    estimated_on: date
    available_at: datetime | None
    value_m3_m3: float | None = None
    research_interval_m3_m3: tuple[float, float] | None = None
    source_sha256: tuple[str, ...] = ()
    model_id: str = "fieldshift-sm05-024c35a39470"
    artifact_sha256: str = "024c35a39470c964c6863af8d73e624ee869d211e5a10f6b954d5d2122dbe914"
    product: str = "historical_surface_reconstruction"
    # Depth of the returned quantity; raw ERA5 fallback overrides to 0–7 cm.
    target_depth_cm: tuple[int, int] = (0, 5)
    grid: str = "ERA5-Land ~0.1 degree; not field-resolution moisture"
    validation_status: str = "experimental_not_independently_reproduced"
    interval_status: str = "source_research_parameters_no_calibrated_coverage_claim"
    promoted: bool = False
