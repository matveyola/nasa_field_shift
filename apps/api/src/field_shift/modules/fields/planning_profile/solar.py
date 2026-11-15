"""Optional pvlib SPA adapter; no substitute geometry or canopy response."""

import math
from dataclasses import dataclass
from datetime import timedelta
from importlib.metadata import version


@dataclass(frozen=True)
class SolarDescriptor:
    zenith_deg: float
    azimuth_deg: float
    global_horizontal_w_m2: float | None
    direct_normal_w_m2: float | None
    diffuse_horizontal_w_m2: float | None
    engine_version: str


def solar_position(instant, latitude, longitude, elevation_m, *, ghi=None, dni=None, dhi=None):
    if instant.utcoffset() != timedelta(0):
        raise ValueError("solar instant must be UTC")
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("latitude/longitude out of range")
    if elevation_m is None or not math.isfinite(elevation_m):
        raise ValueError("elevation is required; do not substitute sea level")
    for value in (ghi, dni, dhi):
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("irradiance must be nonnegative finite W/m²")
    try:
        import pandas as pd
        from pvlib.solarposition import get_solarposition
    except ImportError as exc:
        raise RuntimeError("pvlib is unavailable; solar geometry is unsupported") from exc
    position = get_solarposition(
        pd.DatetimeIndex([instant]), latitude, longitude, altitude=elevation_m, method="nrel_numpy"
    ).iloc[0]
    zenith = float(position["zenith"])
    # No ground-level shortwave at night; daytime missing flux remains unknown.
    if zenith >= 90:
        ghi = dni = dhi = 0.0
    return SolarDescriptor(zenith, float(position["azimuth"]), ghi, dni, dhi, version("pvlib"))
