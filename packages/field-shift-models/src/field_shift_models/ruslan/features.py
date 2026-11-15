"""Narrow inference transform adapted from Ruslan's real.py/robust.py; see NOTICE.

No acquisition, fitting, station labels, L4 substitution or current-year climatology.
The pinned final artifact's reported training inputs contained no L4 matches.
"""

import math
from datetime import timedelta

import numpy as np

from .types import HistoricalRequest, SmapDay


class RejectedSnapshot(ValueError):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason
        super().__init__(reason)


def valid_l3(record: SmapDay):
    return (
        record.surface_vwc is not None
        and 0 < record.surface_vwc <= 0.8
        and type(record.retrieval_quality_flag) is int
        and record.retrieval_quality_flag >= 0
        and record.retrieval_quality_flag & 1 == 0
    )


def _stats(values):
    if len(values) < 30:
        raise RejectedSnapshot("insufficient_data", "At least 30 past-season product days required")
    mean, sd = float(np.mean(values)), float(np.std(values))
    if not math.isfinite(sd) or sd <= 0:
        raise RejectedSnapshot("insufficient_data", "Past-season product variance unavailable")
    return mean, sd


def prepare(request: HistoricalRequest):
    """Return the original 17 features, current ERA5 level and amplitude, or raw fallback."""
    date = request.estimated_on
    era5 = {r.day: r for r in request.era5}
    smap = {r.day: r for r in request.smap if valid_l3(r)}
    recent = [era5.get(date - timedelta(days=k)) for k in range(7)]
    if any(r is None for r in recent):
        raise RejectedSnapshot("insufficient_data", "Seven consecutive ERA5 days required")
    today = recent[0]
    if today.soil_temperature_c is None or today.snow_depth_m is None:
        raise RejectedSnapshot("insufficient_data", "Soil temperature and snow evidence required")
    if today.soil_temperature_c < 2 or today.snow_depth_m >= 0.01:
        raise RejectedSnapshot(
            "unsupported", "Frozen or snow-covered surface outside research scope"
        )
    surface = [r.surface_vwc for r in recent]
    if any(v is None for v in surface[:3]):
        raise RejectedSnapshot("insufficient_data", "Latest three surface moisture days required")
    l3 = next(
        (smap[date - timedelta(days=k)] for k in range(2, 6) if date - timedelta(days=k) in smap),
        None,
    )
    # Source explicitly falls back without an eligible L3 retrieval. No trained interval is added.
    if l3 is None:
        return None, surface[0], None
    if request.soil is None or today.subsurface_vwc is None or today.air_temperature_c is None:
        raise RejectedSnapshot(
            "insufficient_data", "SSURGO soil, subsurface moisture and air temperature required"
        )
    previous = [r for r in request.era5 if r.day.year < date.year and 4 <= r.day.month <= 10]
    previous_l3 = [
        r.surface_vwc for r in smap.values() if r.day.year < date.year and 4 <= r.day.month <= 10
    ]
    e0_mean, e0_sd = _stats([r.surface_vwc for r in previous if r.surface_vwc is not None])
    e1_mean, e1_sd = _stats([r.subsurface_vwc for r in previous if r.subsurface_vwc is not None])
    l3_mean, l3_sd = _stats(previous_l3)
    mean3 = sum(surface[:3]) / 3
    mean7 = float(np.mean([v for v in surface if v is not None]))
    doy = date.timetuple().tm_yday
    soil = request.soil
    values = [
        (surface[0] - e0_mean) / e0_sd,
        (today.subsurface_vwc - e1_mean) / e1_sd,
        (mean3 - e0_mean) / e0_sd,
        (mean7 - e0_mean) / e0_sd,
        (surface[0] - mean7) / e0_sd,
        math.nan,
        math.nan,
        math.nan,  # No SMAP L4 was present in the pinned artifact cohort.
        (l3.surface_vwc - l3_mean) / l3_sd,
        (date - l3.day).days,
        math.sin(2 * math.pi * doy / 365.25),
        math.cos(2 * math.pi * doy / 365.25),
        today.soil_temperature_c,
        today.air_temperature_c,
        soil.sand_percent,
        soil.clay_percent,
        soil.organic_matter_percent,
    ]
    if not all(math.isfinite(v) for i, v in enumerate(values) if i not in (5, 6, 7)):
        raise RejectedSnapshot("insufficient_data", "Nonfinite anomaly features")
    return values, surface[0], e0_sd
