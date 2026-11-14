"""AquaCrop-OSPy wrapper; no crop physiology or hidden provider fetches here."""

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from aquacrop import AquaCropModel, Crop, InitialWaterContent, IrrigationManagement, Soil

# Exact names in the pinned upstream crop dictionary; no substitution across species.
NATIVE_CROP_NAMES = {
    "Maize": "Maize",
    "Wheat": "Wheat",
    "Barley": "Barley",
    "Soy": "Soybean",
    "Sunflower": "Sunflower",
}


class RejectedInput(ValueError):
    def __init__(self, status, code, message):
        self.status, self.code = status, code
        super().__init__(message)


@dataclass(frozen=True)
class Parameters:
    """Explicit hydraulic assumptions not present in planning-v1 SoilHorizon.

    Saturated conductivity and penetrability must come from a permissioned parameter
    source; passing synthetic values is allowed only for explicitly synthetic runs.
    """

    ksat_mm_day: tuple[float, ...]
    penetrability_percent: tuple[float, ...]
    source: str
    synthetic: bool
    irrigation_events: tuple[tuple[date, float], ...] = ()
    uniform_initial_profile: bool = False

    def __post_init__(self):
        if type(self.synthetic) is not bool or type(self.uniform_initial_profile) is not bool:
            raise ValueError("Synthetic and initial-profile assumptions require explicit booleans")
        if not isinstance(self.source, str):
            raise ValueError("Parameter source must be a provenance identifier")
        if any(type(d) is not date for d, _ in self.irrigation_events):
            raise ValueError("Irrigation events require daily dates")
        if (
            len(self.irrigation_events) > 1000
            or any(not np.isfinite(v) or v < 0 for _, v in self.irrigation_events)
            or len({d for d, _ in self.irrigation_events}) != len(self.irrigation_events)
        ):
            raise ValueError("Invalid bounded irrigation schedule")
        if (
            not self.source
            or not self.ksat_mm_day
            or (len(self.ksat_mm_day) != len(self.penetrability_percent))
        ):
            raise ValueError("hydraulic parameter provenance and aligned layers required")
        if any(not np.isfinite(v) or v <= 0 for v in self.ksat_mm_day) or any(
            not np.isfinite(v) or not 0 < v <= 100 for v in self.penetrability_percent
        ):
            raise ValueError("invalid hydraulic parameters")


@dataclass
class EngineResult:
    dates: list[date]
    theta: np.ndarray
    flux: pd.DataFrame
    growth: pd.DataFrame
    harvests: pd.DataFrame
    dz_m: np.ndarray
    wp: np.ndarray
    fc: np.ndarray
    max_balance_error_mm: float


def forcing_frame(days):
    records = []
    for day in days:
        if day.quality != "valid" or any(
            getattr(day, name) is None for name in ("tmin_c", "tmax_c", "liquid_rain_mm", "et0_mm")
        ):
            raise RejectedInput(
                "insufficient_data", "missing_weather", "Complete daily forcing required"
            )
        if day.soil_frozen is None or day.snow_water_equivalent_mm is None:
            raise RejectedInput(
                "insufficient_data", "winter_unknown", "Snow/frozen status is unknown"
            )
        if day.soil_frozen or day.snow_water_equivalent_mm > 0 or day.tmin_c < 0:
            raise RejectedInput(
                "unsupported", "winter", "This adapter has no validated winter routine"
            )
        # OSPy consumes positional columns in precisely this order.
        records.append(
            (day.tmin_c, day.tmax_c, day.liquid_rain_mm, day.et0_mm, pd.Timestamp(day.date))
        )
    return pd.DataFrame(
        records, columns=["MinTemp", "MaxTemp", "Precipitation", "ReferenceET", "Date"]
    )


def run_engine(profile, assignments, days, parameters, *, crop_wp=None):
    first, last = assignments[0], assignments[-1]
    if not first.sowing_window or not last.harvest_window:
        raise RejectedInput(
            "insufficient_data", "calendar", "Explicit sowing and harvest dates required"
        )
    sow, end = first.sowing_window.start, last.harvest_window.end
    if any(
        not a.sowing_window
        or not a.harvest_window
        or (
            a.sowing_window.start != a.sowing_window.end
            or a.harvest_window.start != a.harvest_window.end
            or a.harvest_window.end.year != a.season
            or a.sowing_window.start.year != a.season
            or a.sowing_window.start >= a.harvest_window.end
        )
        for a in assignments
    ):
        raise RejectedInput(
            "unsupported", "calendar", "Only exact same-year annual calendars supported"
        )
    if any(
        (a.sowing_window.start.strftime("%m/%d"), a.harvest_window.end.strftime("%m/%d"), a.crop)
        != (sow.strftime("%m/%d"), end.strftime("%m/%d"), first.crop)
        for a in assignments
    ):
        raise RejectedInput(
            "unsupported", "rotation", "OSPy supports repeated crop/calendar, not rotations"
        )
    water = profile.water_state
    opening_date = water.observed_at.date() if water else sow
    if opening_date > sow:
        raise RejectedInput(
            "insufficient_data", "opening_water", "Opening state must precede sowing"
        )
    selected = [d for d in days if opening_date <= d.date <= end]
    if not selected or selected[0].date != opening_date or selected[-1].date != end:
        raise RejectedInput(
            "insufficient_data", "weather_window", "Forcing must cover sowing through harvest"
        )
    weather = forcing_frame(selected)
    horizons = profile.soil
    if not horizons or len(horizons) != len(parameters.ksat_mm_day):
        raise RejectedInput(
            "insufficient_data", "soil", "Hydraulics required for each soil horizon"
        )
    crop = Crop(
        NATIVE_CROP_NAMES[first.crop.crop_id],
        planting_date=sow.strftime("%m/%d"),
        harvest_date=end.strftime("%m/%d"),
    )
    if crop_wp is not None:
        if not np.isfinite(crop_wp) or crop_wp <= 0:
            raise ValueError("Offline water-productivity candidate must be positive and finite")
        crop.WP = float(crop_wp)
    dz, wp, fc, sats = [], [], [], []
    soil = None
    previous = 0
    for h in horizons:
        if (
            h.top_cm != previous
            or h.quality != "valid"
            or any(
                v is None
                for v in (h.wilting_point_m3_m3, h.field_capacity_m3_m3, h.saturation_m3_m3)
            )
        ):
            raise RejectedInput(
                "insufficient_data", "soil", "Contiguous valid hydraulic horizons required"
            )
        if h.salinity_ds_m is None:
            raise RejectedInput(
                "insufficient_data", "salinity_unknown", "Salinity assessment required"
            )
        if h.salinity_ds_m > 0:
            raise RejectedInput(
                "unsupported", "salinity", "No validated salinity response in this adapter"
            )
        thickness = (h.bottom_cm - h.top_cm) / 100
        # Compartment boundaries never straddle a supplied soil horizon.
        count = int(np.ceil(thickness / 0.1))
        dz.extend([thickness / count] * count)
        wp.extend([h.wilting_point_m3_m3] * count)
        fc.extend([h.field_capacity_m3_m3] * count)
        sats.extend([h.saturation_m3_m3] * count)
        previous = h.bottom_cm
    if len(dz) > 100 or sum(dz) < crop.Zmax + 0.1:
        raise RejectedInput(
            "insufficient_data", "soil_depth", "Soil must cover maximum roots + 10 cm"
        )
    soil = Soil("custom", dz=dz)
    for h, k, p in zip(horizons, parameters.ksat_mm_day, parameters.penetrability_percent):
        soil.add_layer(
            (h.bottom_cm - h.top_cm) / 100,
            h.wilting_point_m3_m3,
            h.field_capacity_m3_m3,
            h.saturation_m3_m3,
            k,
            p,
        )
    water = profile.water_state
    if (
        not water
        or water.volumetric_m3_m3 is None
        or water.depth_top_cm != 0
        or (abs(water.depth_bottom_cm / 100 - sum(dz)) > 1e-9)
        or water.observed_at.time().isoformat() != "00:00:00"
        or water.available_at > profile.as_of
    ):
        raise RejectedInput(
            "insufficient_data", "opening_water", "Dated full-profile opening VWC required"
        )
    if any(water.volumetric_m3_m3 > hi for hi in sats):
        raise RejectedInput(
            "unsupported", "opening_water", "Opening VWC outside modeled soil limits"
        )
    if not parameters.uniform_initial_profile:
        raise RejectedInput(
            "insufficient_data",
            "opening_profile",
            "Depth-averaged VWC needs an explicit uniform opening-profile assumption",
        )
    available = float(
        (
            np.clip(water.volumetric_m3_m3 - np.array(wp), 0, np.array(fc) - np.array(wp))
            * np.array(dz)
            * 1000
        ).sum()
    )
    capacity = float(((np.array(fc) - np.array(wp)) * np.array(dz) * 1000).sum())
    if any(
        actual is not None and abs(actual - expected) > 1e-6
        for actual, expected in (
            (water.available_reserve_mm, available),
            (water.available_capacity_mm, capacity),
        )
    ):
        raise RejectedInput("unsupported", "water_units", "Opening VWC/reserve/capacity disagree")
    if parameters.synthetic and any(s.kind != "synthetic" for s in profile.sources):
        raise RejectedInput(
            "unsupported", "synthetic_parameters", "Synthetic hydraulics cannot serve real fields"
        )
    # v1 has an availability ceiling, not an application schedule. Require external
    # permissioned scheduled NET ground applications; never spend the ceiling silently.
    limits = {i.season: i for i in profile.management.irrigation}
    for a in assignments:
        limit = limits.get(a.season)
        events = [
            (d, v)
            for d, v in parameters.irrigation_events
            if a.sowing_window.start <= d <= a.harvest_window.end
        ]
        if not limit or limit.max_mm is None:
            raise RejectedInput(
                "insufficient_data", "irrigation_limit", "Explicit irrigation ceiling required"
            )
        if limit.system not in ("none", "sprinkler"):
            raise RejectedInput(
                "unsupported", "irrigation_system", "Only rainfed or uniform sprinkler modeled"
            )
        if sum(v for _, v in events) > limit.max_mm:
            raise RejectedInput(
                "unsupported", "irrigation_budget", "Scheduled irrigation exceeds availability"
            )
        if limit.system != "none" and not events:
            raise RejectedInput(
                "insufficient_data", "irrigation_schedule", "Dated net irrigation schedule required"
            )
    if any(
        not any(a.sowing_window.start <= d <= a.harvest_window.end for a in assignments)
        for d, _ in parameters.irrigation_events
    ):
        raise RejectedInput(
            "unsupported", "irrigation_calendar", "Applications must fall in supplied crop windows"
        )
    schedule = pd.DataFrame(
        [(pd.Timestamp(d), v) for d, v in parameters.irrigation_events], columns=["Date", "Depth"]
    )
    irrigation = IrrigationManagement(
        3,
        Schedule=schedule,
        AppEff=100.0,
        MaxIrr=max([v for _, v in parameters.irrigation_events] + [25.0]),
        MaxIrrSeason=max([i.max_mm for i in limits.values() if i.max_mm is not None] + [0.0]),
    )
    model = AquaCropModel(
        opening_date.strftime("%Y/%m/%d"),
        end.strftime("%Y/%m/%d"),
        weather,
        soil,
        crop,
        InitialWaterContent(
            wc_type="Num",
            method="Depth",
            depth_layer=[0.0, sum(dz)],
            value=[water.volumetric_m3_m3] * 2,
        ),
        irrigation_management=irrigation,
        off_season=True,
    )
    model.run_model(till_termination=True)
    # OSPy allocates trailing zero rows after its early final-harvest termination.
    harvests = model.get_simulation_results()
    if len(harvests) != len(assignments):
        raise RuntimeError("Engine did not produce every requested harvest")
    n = int(harvests.iloc[-1]["Harvest Date (Step)"]) + 1
    flux = model.get_water_flux().iloc[:n].copy()
    growth = model.get_crop_growth().iloc[:n].copy()
    theta = model.get_water_storage().iloc[:n].filter(regex=r"^th\d+$").to_numpy()
    dz, wp, fc = np.array(dz), np.array(wp), np.array(fc)
    if theta.shape != (n, len(dz)) or n > len(selected):
        raise RuntimeError("Engine changed compartment layout or execution window")
    storage = (theta * dz * 1000).sum(axis=1) + flux.surface_storage.to_numpy()
    opening = np.r_[water.volumetric_m3_m3 * dz.sum() * 1000, storage[:-1]]
    inputs = weather.Precipitation.to_numpy()[:n] + flux.IrrDay + flux.CR + flux.GwIn
    outputs = flux.Runoff + flux.DeepPerc + flux.TileDrain + flux.Es + flux.Tr
    residual = storage - opening - inputs + outputs
    error = float(np.max(np.abs(residual)))
    if not np.isfinite(theta).all() or not np.isfinite(error) or error > 1e-6:
        raise RuntimeError("Engine failed daily water conservation")
    return EngineResult(
        [d.date for d in selected[:n]], theta, flux, growth, harvests, dz, wp, fc, error
    )
