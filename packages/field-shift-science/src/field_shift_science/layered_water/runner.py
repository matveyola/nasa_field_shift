"""Strict experimental water simulation with preserved state across mixed calendars.

Incoming precipitation is TOTAL liquid-equivalent precipitation. The public
planning-v1 liquid_rain_mm field alone cannot supply this winter experiment.
Snow phase partition, melt, freeze and runoff are explicit model assumptions.
No yield, nitrogen, flood-depth, paddy or calibrated crop-risk prediction is made.
"""

from dataclasses import dataclass
from datetime import date
from math import isfinite

from .balance import DayResult, Profile, simulate
from .planting import CalendarParameters, Planting
from .soil import SoilHydraulics
from .weather import WeatherDay


@dataclass(frozen=True)
class LayeredParameters:
    field_capacity: tuple[float, ...]
    wilting_point: tuple[float, ...]
    saturation: tuple[float, ...]
    initial_vwc: tuple[float, ...]
    drainage_fraction: tuple[float, ...]
    curve_numbers: dict[str, float]
    snow_temperature_c: float
    melt_mm_per_c_day: float
    freeze_tmean_7d_c: float
    frozen_curve_number: float
    initial_residue_cover: float
    initial_snow_mm: float
    prior_tmean_c: tuple[float, ...]
    source: str

    def __post_init__(self):
        n = len(self.field_capacity)
        vectors = (
            self.field_capacity,
            self.wilting_point,
            self.saturation,
            self.initial_vwc,
            self.drainage_fraction,
        )
        if not self.source.strip() or not 1 <= n <= 100 or any(len(v) != n for v in vectors):
            raise ValueError("Aligned 10cm hydraulic layers and provenance required")
        for fc, wp, sat, theta, drain in zip(*vectors, strict=True):
            if not all(isfinite(v) for v in (fc, wp, sat, theta, drain)):
                raise ValueError("Finite hydraulics required")
            if not 0 <= wp < fc <= sat <= 1 or not wp <= theta <= sat or not 0 <= drain <= 1:
                raise ValueError("Invalid layer hydraulics or opening water")
        if not isfinite(self.initial_snow_mm) or not 0 <= self.initial_snow_mm <= 10000:
            raise ValueError("Explicit finite nonnegative opening snow required")
        if len(self.prior_tmean_c) != 7 or any(
            not isfinite(v) or not -100 <= v <= 65 for v in self.prior_tmean_c
        ):
            raise ValueError("Seven preceding daily temperatures required for freeze state")
        if set(self.curve_numbers) != {"row", "small_grain", "residue", "bare"}:
            raise ValueError("Explicit runoff coefficients required for all cover classes")
        if any(not isfinite(v) or not 0 < v <= 100 for v in self.curve_numbers.values()):
            raise ValueError("Invalid curve numbers")
        values = (
            self.snow_temperature_c,
            self.melt_mm_per_c_day,
            self.freeze_tmean_7d_c,
            self.frozen_curve_number,
            self.initial_residue_cover,
        )
        if not all(isfinite(v) for v in values) or not 0 <= self.melt_mm_per_c_day <= 20:
            raise ValueError("Invalid snow assumptions")
        if not -20 <= self.snow_temperature_c <= 5 or not -30 <= self.freeze_tmean_7d_c <= 5:
            raise ValueError("Invalid freeze assumptions")
        if not 0 < self.frozen_curve_number <= 100 or not 0 <= self.initial_residue_cover <= 1:
            raise ValueError("Invalid runoff or residue assumptions")


@dataclass(frozen=True)
class WaterRun:
    days: tuple[DayResult, ...]
    max_balance_error_mm: float
    initial_profile_mm: float
    closing_profile_mm: float
    closing_snow_mm: float
    closing_vwc: tuple[float, ...]
    sources: tuple[str, ...]
    status: str = "experimental_water_only"


def run_water_balance(
    parameters: LayeredParameters,
    weather: list[WeatherDay],
    calendars: list[CalendarParameters],
    *,
    irrigation: dict[date, float] | None = None,
    land_status: str = "unknown",
) -> WaterRun:
    if land_status != "arable":
        raise ValueError("Confirmed arable land required; ice/ocean/unknown are not crop sites")
    if not weather or len(weather) > 2192 or len(calendars) > 12:
        raise ValueError("Bounded nonempty forcing and at most twelve crop calendars required")
    dates = [d.date for d in weather]
    if any((b - a).days != 1 for a, b in zip(dates, dates[1:])):
        raise ValueError("Weather must be sorted, unique and complete every day")
    for day in weather:
        if not day.source or day.gap:
            raise ValueError("Complete provenance-backed weather required; no gap filling")
        values = (day.tmax, day.tmin, day.rain_mm, day.et0_mm)
        if not all(isfinite(v) for v in values) or day.tmin > day.tmax:
            raise ValueError("Invalid daily weather")
        if (
            not -100 <= day.tmin <= day.tmax <= 65
            or not 0 <= day.rain_mm <= 3000
            or not 0 <= day.et0_mm <= 100
        ):
            raise ValueError("Weather outside declared physical range")
        if day.rhmin is not None and (not isfinite(day.rhmin) or not 0 <= day.rhmin <= 100):
            raise ValueError("RHmin must be percent, not mean relative humidity")
        if day.u2 is not None and (not isfinite(day.u2) or not 0 <= day.u2 <= 150):
            raise ValueError("Wind must be finite at 2m height")
    ordered = sorted(calendars, key=lambda c: c.sowing)
    for calendar in ordered:
        if calendar.crop_id.lower() in {"rice", "paddy"}:
            raise ValueError("Paddy requires a separately validated surface-water model")
        if calendar.sowing < dates[0] or calendar.harvest > dates[-1]:
            raise ValueError("Weather must cover full crop calendars and interseason gaps")
        if calendar.root_max_m > len(parameters.field_capacity) * 0.1 + 1e-10:
            raise ValueError("Soil profile must cover every crop root depth")
    if any(a.harvest >= b.sowing for a, b in zip(ordered, ordered[1:])):
        raise ValueError("Overlapping calendars/intercropping are unsupported")
    irrig = irrigation or {}
    if any(d not in set(dates) or not isfinite(v) or v < 0 for d, v in irrig.items()):
        raise ValueError("Irrigation must be finite nonnegative and inside forcing window")
    soil = SoilHydraulics(
        parameters.field_capacity[0], parameters.wilting_point[0], parameters.source
    )
    profile = Profile(
        soil,
        depth_m=len(parameters.field_capacity) * 0.1,
        relative_wetness=0,
        layer_awc=[
            fc - wp
            for fc, wp in zip(parameters.field_capacity, parameters.wilting_point, strict=True)
        ],
        layer_gravity=[
            sat - fc
            for sat, fc in zip(parameters.saturation, parameters.field_capacity, strict=True)
        ],
        drain=parameters.drainage_fraction,
    )
    profile.w = [
        (theta - wp) * 100
        for theta, wp in zip(parameters.initial_vwc, parameters.wilting_point, strict=True)
    ]
    profile.snow_mm = parameters.initial_snow_mm
    profile.tmean_hist = list(parameters.prior_tmean_c)
    opening = profile.total()
    rows = simulate(
        profile,
        weather,
        [Planting(c) for c in ordered],
        irrigation=irrig,
        bare_reference_m=profile.depth_m,
        curve_numbers=parameters.curve_numbers,
        snow_temperature_c=parameters.snow_temperature_c,
        melt_mm_per_c_day=parameters.melt_mm_per_c_day,
        freeze_tmean_7d_c=parameters.freeze_tmean_7d_c,
        frozen_curve_number=parameters.frozen_curve_number,
        initial_residue_cover=parameters.initial_residue_cover,
    )
    prior = opening
    prior_snow = parameters.initial_snow_mm
    error = 0.0
    for row in rows:
        residual = (
            prior
            + prior_snow
            + row.rain_mm
            + row.irrigation_mm
            - row.runoff_mm
            - row.eta_mm
            - row.drainage_mm
            - row.profile_mm
            - row.snow_mm
        )
        error = max(error, abs(residual))
        prior, prior_snow = row.profile_mm, row.snow_mm
    if error > 1e-6:
        raise RuntimeError("Daily soil+snow mass balance failed")
    closing = tuple(w / 100 + wp for w, wp in zip(profile.w, parameters.wilting_point, strict=True))
    return WaterRun(
        tuple(rows),
        error,
        opening,
        profile.total(),
        profile.snow_mm,
        closing,
        tuple(
            sorted(
                {parameters.source, *[c.source for c in calendars], *[d.source for d in weather]}
            )
        ),
    )
