"""Compare one growing season after replaying the same field and selected crops."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, timedelta
from functools import cache
from itertools import pairwise
from math import floor, fsum, isfinite
from pathlib import Path
from statistics import mean
from threading import Lock
from types import SimpleNamespace

from .hybrid_profiles import crop_catalog, resolve_profile, strategy_hybrids
from .supplied_balance import BalanceDay, summarize_days


class ClimateBalanceError(ValueError):
    """Shared inputs or prior crop history cannot form a comparison."""


NASA = "NASA NEX-GDDP-CMIP6 (https://doi.org/10.7917/OFSG3345)"
FAO56 = "https://www.fao.org/4/X0490E/X0490E00.htm"
AQUACROP = "https://ees.kuleuven.be/en/aquacrop/resources/reference-manual/annexesaquacropv73.pdf"
INITIAL_AVAILABLE_FRACTION = 0.5
DRAIN_FRACTION = 0.1
THERMAL_PARAMETERS = {
    "maize_grain": (8.0, 30.0, "I.2 Maize", "row", "C09"),
    "sunflower": (4.0, 30.0, "I.8 Sunflower", "row", "C11"),
    "soybeans": (5.0, 30.0, "I.6 Soybean", "row", "C12-35"),
    "wheat_spring": (0.0, 26.0, "I.10 Wheat", "small_grain", "C01"),
    "barley": (0.0, 15.0, "I.11 Barley", "small_grain", "C01"),
    "potato": (2.0, 26.0, "I.3 Potato", "row", "C19"),
    "sugar_beet": (5.0, 30.0, "I.7 Sugar Beet", "row", "C15"),
}
SUPPORTED_CROPS = tuple(THERMAL_PARAMETERS)
STRATEGY_PROFILES = {
    # Use each crop's documented thermal optimum as the cap for this simple GDD model.
    "peas_dry": (0.0, 25.0, "https://library.dpird.wa.gov.au/books/41/", "row", "C13"),
    "oats": (0.0, 26.0, "https://doi.org/10.3389/fagro.2025.1701976", "small_grain", "C02"),
    "millet": (
        10.0,
        30.0,
        "https://www.ars.usda.gov/ARSUserFiles/30100000/2009Documents/2009/491.pdf",
        "small_grain",
        "C22",
    ),
}
STRATEGY_CROPS = SUPPORTED_CROPS + tuple(STRATEGY_PROFILES)
MAX_STRATEGY_STEPS = 6
MAX_STRATEGY_CANDIDATES = len(STRATEGY_CROPS) + len(strategy_hybrids())
_ENGINE_LOCK = Lock()


def engine_directory() -> Path:
    """Compatibility identifier for the installed engine, never an override."""
    from importlib.resources import files

    return Path(str(files("field_shift_rotation_model.ruslan")))


def _as_date(value):
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ClimateBalanceError(f"Invalid calendar date {value!r}.")


def _number(value, name, minimum, maximum):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ClimateBalanceError(f"Invalid {name}: expected {minimum:g} to {maximum:g}.")
    return float(value)


def _date_range(first, last):
    return (first + timedelta(days=offset) for offset in range((last - first).days + 1))


def required_weather_dates(planting_date, selected=(), *, strategy=False):
    """Return inclusive weather dates for the common simulation and spin-up."""
    if strategy and len(selected) >= MAX_STRATEGY_STEPS:
        raise ClimateBalanceError("At most five previous strategy crops can be replayed.")
    if not strategy and len(selected) > 2:
        raise ClimateBalanceError("At most two previous crops can be replayed.")
    dates = [_as_date(row.get("planting_date")) for row in selected] + [_as_date(planting_date)]
    if any(not (3 <= day.month <= 7) for day in dates):
        raise ClimateBalanceError("This mode supports March through July sowing.")
    if strategy and any(later <= earlier for earlier, later in pairwise(dates)):
        raise ClimateBalanceError("Sowing dates must be in ascending order.")
    if not strategy and any(later.year <= earlier.year for earlier, later in pairwise(dates)):
        raise ClimateBalanceError("Use one sowing per year, in ascending order.")
    if strategy and dates[-1].year - dates[0].year > 9:
        raise ClimateBalanceError("Strategy decisions may span up to ten calendar years.")
    if not strategy and dates[-1].year - dates[0].year > 4:
        raise ClimateBalanceError("The three farm decisions can span up to five calendar years.")
    if dates[0].year <= 1 or dates[-1].year >= 9999:
        raise ClimateBalanceError("The planting year needs room for spin-up and next-year dates.")
    return date(dates[0].year - 1, 10, 1), date(dates[-1].year, 12, 31)


def _matching_coordinates(payload, latitude, longitude, source):
    payload_lat = _number(payload.get("latitude"), f"{source} latitude", -90, 90)
    payload_lon = _number(payload.get("longitude"), f"{source} longitude", -180, 180)
    longitude_difference = (payload_lon - longitude + 180) % 360 - 180
    if abs(payload_lat - latitude) > 0.02 or abs(longitude_difference) > 0.02:
        raise ClimateBalanceError(f"{source} request coordinates must match the selected field.")


def _historical_reference(payload, latitude, longitude):
    """Convert complete historical daily Tmax/Tmin to a smoothed local reference."""
    if "geometry" in payload:
        west, north, altitude = payload["geometry"]["coordinates"]
        header = payload["header"]
        parameters = payload["properties"]["parameter"]
        maximum, minimum = parameters["T2M_MAX"], parameters["T2M_MIN"]
        request_url = payload.get("request_url")
        if request_url is None and isinstance(payload.get("source"), dict):
            request_url = payload["source"].get("url")
        payload = {
            "latitude": north,
            "longitude": west,
            "elevation_m": altitude,
            "source": "NASA POWER daily T2M_MAX and T2M_MIN (MERRA-2)",
            "source_url": "https://power.larc.nasa.gov/docs/services/api/temporal/daily/",
            "request_url": request_url,
            "start": header["start"],
            "end": header["end"],
            "daily": [
                {"date": key, "tmax_c": high, "tmin_c": minimum.get(key)}
                for key, high in maximum.items()
            ],
        }
    _matching_coordinates(payload, latitude, longitude, "Historical temperature")
    elevation = _number(payload.get("elevation_m"), "historical point elevation (m)", -500, 6000)
    source = payload.get("source")
    if not source:
        raise ClimateBalanceError("Historical temperature data needs a source.")
    start, end = _as_date(payload.get("start")), _as_date(payload.get("end"))
    if (start.month, start.day) != (1, 1) or (end.month, end.day) != (12, 31) or start > end:
        raise ClimateBalanceError("Historical temperature must cover whole calendar years.")
    samples = defaultdict(list)
    daily = {}
    for row in payload["daily"]:
        day = _as_date(row["date"])
        if day in daily or not start <= day <= end:
            raise ClimateBalanceError(
                "Historical temperature contains a duplicate or out-of-period date."
            )
        high = _number(row.get("tmax_c"), f"historical Tmax on {day}", -70, 65)
        low = _number(row.get("tmin_c"), f"historical Tmin on {day}", -70, high)
        daily[day] = high, low
        if (day.month, day.day) != (2, 29):
            samples[day.month, day.day].append(daily[day])
    if len(daily) != (end - start).days + 1:
        raise ClimateBalanceError("Historical temperature is missing at least one day.")
    calendar = [(day.month, day.day) for day in _date_range(date(2001, 1, 1), date(2001, 12, 31))]
    table = {}
    for index, key in enumerate(calendar):
        pairs = [
            pair
            for offset in range(-15, 16)
            for pair in samples[calendar[(index + offset) % len(calendar)]]
        ]
        table[key] = tuple(map(mean, zip(*pairs, strict=True)))
    table[2, 29] = tuple(map(mean, zip(table[2, 28], table[3, 1], strict=True)))
    metadata = {
        "source": source,
        "source_url": payload.get("source_url"),
        "request_url": payload.get("request_url"),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "elevation_m": elevation,
        "method": "Shared 31-day moving mean of daily maximum and minimum temperature.",
    }
    return lambda day: table[day.month, day.day], metadata


def _profile_layers(soil_layers, layer_depth):
    """Weight contiguous horizons by thickness into Ruslan's 10 cm layers."""
    if not soil_layers:
        raise ClimateBalanceError("A sourced soil water-capacity profile is required.")
    intervals = []
    last = 0.0
    for layer in soil_layers:
        top = _number(layer.get("top_m"), "soil horizon top", 0, 4)
        bottom = _number(layer.get("bottom_m"), "soil horizon bottom", 0, 4)
        fc = _number(layer.get("field_capacity"), "soil field capacity", 0, 1)
        wp = _number(layer.get("wilting_point"), "soil wilting point", 0, 1)
        if abs(top - last) > 1e-8 or bottom <= top or fc <= wp:
            raise ClimateBalanceError(
                "Soil horizons must be contiguous from the surface with field "
                "capacity above wilting point."
            )
        saturation = layer.get("saturation")
        if saturation is not None and _number(saturation, "soil saturation", 0, 1) <= fc:
            raise ClimateBalanceError("Saturation must exceed field capacity.")
        intervals.append((top, bottom, fc, wp, saturation))
        last = bottom
    n = floor(last / layer_depth + 1e-8)
    if n < 2:
        raise ClimateBalanceError("At least 0.2 m of contiguous soil hydraulics is required.")
    delayed = all(row[4] is not None for row in intervals)
    awc, gravitational = [], []
    for index in range(n):
        lower, upper = index * layer_depth, (index + 1) * layer_depth
        available = gravity = 0.0
        for top, bottom, fc, wp, saturation in intervals:
            overlap = max(0.0, min(upper, bottom) - max(lower, top))
            available += overlap * (fc - wp)
            if delayed:
                gravity += overlap * (saturation - fc)
        awc.append(available / layer_depth)
        gravitational.append(gravity / layer_depth)
    return intervals[0], n * layer_depth, awc, gravitational if delayed else None


def _load_engine(ruslan_dir):
    """Only the installed, source-receipted engine can execute; no path injection."""
    if ruslan_dir != engine_directory():
        raise ClimateBalanceError("Only the installed pinned engine is supported")
    from field_shift_rotation_model.ruslan import balance, crops, meteo, soil, weather

    return SimpleNamespace(balance=balance, crops=crops, meteo=meteo, soil=soil, weather=weather)


@contextmanager
def _local_phenology(engine, historical_temperatures):
    """Ruslan stores crop definitions and temperature calibration at module scope."""
    originals = engine.crops.CROPS.copy()
    original_temperatures = engine.weather._NORMALS
    calibrate = engine.crops.thermal_stages
    hybrid_thresholds = {}

    @cache
    def thermal_stages(key):
        return hybrid_thresholds[key] if key in hybrid_thresholds else calibrate(key)

    engine.weather.set_normals(historical_temperatures)
    engine.crops.reset_calibration()
    engine.crops.thermal_stages = thermal_stages
    try:
        yield hybrid_thresholds
    finally:
        engine.crops.thermal_stages = calibrate
        engine.crops.CROPS.clear()
        engine.crops.CROPS.update(originals)
        engine.weather.set_normals(original_temperatures)
        engine.crops.reset_calibration()


def _crop_record(
    engine, catalogue, choice, day, depth, tag, *, strategy=False, hybrid_thresholds=None
):
    crop_id = choice.get("crop_id")
    parameters = THERMAL_PARAMETERS.get(crop_id)
    thermal_source = None
    if parameters is None and strategy:
        parameters = STRATEGY_PROFILES.get(crop_id)
        if parameters:
            thermal_source = parameters[2]
    if parameters is None:
        raise ClimateBalanceError(f"No supported spring/summer thermal profile for {crop_id!r}.")
    base, cap, thermal_reference, cover_class, proxy = parameters
    hybrid = strategy_hybrids().get(choice.get("choice_id")) if strategy else None
    profile = catalogue[crop_id]
    stage_lengths = choice.get("stage_lengths")
    calendar_id = choice.get("calendar_id")
    if stage_lengths is None and calendar_id is None:
        calendar_id = proxy
    resolved = resolve_profile(crop_id, calendar_id=calendar_id, stage_lengths=stage_lengths)
    if sum(resolved.stage_lengths) > 365:
        raise ClimateBalanceError(f"The stages for {crop_id!r} exceed the annual crop duration.")
    calendar = next((option for option in profile.calendars if option.id == calendar_id), None)
    stage_source = (
        f"{profile.coefficient_source_url} (FAO-56, Table 11, {calendar_id})"
        if calendar
        else choice.get("stage_lengths_source") or "User-supplied durations (source not provided)"
    )
    thermal_source = thermal_source or f"{AQUACROP} (Annex {thermal_reference})"
    flowering = None
    if hybrid:
        base, cap = hybrid["base_c"], hybrid["cap_c"]
        flowering = hybrid["flowering_gdd"]
        maturity = hybrid["maturity_gdd"]
        thermal_source = hybrid["source"]
        initial, development, mid, late = resolved.stage_lengths
        # The sources anchor flowering and maturity. Split the intervals with the
        # corresponding FAO-56 calendar proportions for the water-use curve.
        hybrid_thresholds[f"field_shift_climate_{tag}"] = (
            round(flowering * initial / (initial + development), 2),
            flowering,
            round(flowering + (maturity - flowering) * mid / (mid + late), 2),
            maturity,
        )
    val = engine.crops.Val
    omitted = val(None, "Outside the water balance outputs.")
    user_date = val((day.month, day.day), "Supplied sowing date")
    mmdd = day.strftime("%m-%d")
    maximum_root = min(resolved.root_depth_m, depth)
    kc_ini, kc_mid, kc_end = (
        val(value, profile.coefficient_source_url) for value in resolved.coefficients
    )
    record = engine.crops.CropRecord(
        key=f"field_shift_climate_{tag}",
        name_en=crop_id,
        name_uk=profile.name,
        group=val("summer annual", "Supported set"),
        region=val(None, "User-supplied field"),
        kc_ini=kc_ini,
        kc_mid=kc_mid,
        kc_end=kc_end,
        height_m=omitted,
        stages_days=val(resolved.stage_lengths, stage_source),
        root_min_m=val(min(0.15, maximum_root), engine.crops.FAO56_CH8),
        root_max_m=val(maximum_root, profile.root_depth_source_url),
        p=val(resolved.depletion_fraction, profile.root_depth_source_url),
        ky=omitted,
        planting=user_date,
        planting_range=val((mmdd, mmdd), "Supplied sowing date"),
        end_event=val("harvest", "Season termination per Ruslan model"),
        flowering=val({"method": "gdd", "gdd_c": flowering, "window_days": 1}, thermal_source)
        if hybrid
        else omitted,
        sensitive_phase=omitted,
        heat_threshold_c=omitted,
        legume=val(crop_id in {"soybeans", "peas_dry"}, "Botany"),
        cover_class=val(cover_class, engine.crops.TR55, "adapted"),
        winter=val(False, "Supported set"),
        gdd_base_c=val(base, thermal_source),
        gdd_cap_c=val(cap, thermal_source),
    )
    engine.crops.CROPS[record.key] = record
    thresholds = engine.crops.thermal_stages(record.key)
    if any(current <= previous for previous, current in zip((0, *thresholds), thresholds)):
        raise ClimateBalanceError(
            f"Local reference is too cold for all development stages of {crop_id!r} on {day}."
        )
    return record, {
        "calendar_id": calendar_id,
        "calendar_kind": "hybrid_thermal_reference"
        if hybrid
        else "reference_proxy"
        if calendar
        else "supplied_durations",
        "reference_region": calendar.reference_region if calendar else None,
        "reference_planting_months": calendar.reference_planting_months if calendar else None,
        "stage_lengths_days": resolved.stage_lengths,
        "stage_lengths_source": stage_source,
        "base_temperature_c": base,
        "cap_temperature_c": cap,
        "thermal_source": thermal_source,
        **({"hybrid_thermal_reference": hybrid} if hybrid else {}),
        "calibrated_stage_end_degree_days_c": thresholds,
        "root_depth_m": maximum_root,
        "root_depth_source": profile.root_depth_source_url,
        "crop_coefficients": resolved.coefficients,
        "crop_coefficients_source": profile.coefficient_source_url,
        "depletion_fraction": resolved.depletion_fraction,
    }


def _weather_series(payload, latitude, longitude, elevation, start, end, engine):
    from field_shift_rotation_model.replay_marsden_climate import projected_weather

    _matching_coordinates(payload, latitude, longitude, "Climate weather")
    if not payload.get("series"):
        raise ClimateBalanceError("At least one daily climate series is required.")
    metadata, weather = {}, {}
    for series in payload["series"]:
        key = "__".join(str(series[name]) for name in ("model", "scenario", "member"))
        if key in weather:
            raise ClimateBalanceError(f"Duplicate climate member {key!r}.")
        rows = [row for row in series["daily"] if start <= _as_date(row["date"]) <= end]
        try:
            by_day = projected_weather(
                dict(series, daily=rows), latitude, elevation, engine.meteo, engine.weather
            )
        except (KeyError, ValueError) as error:
            raise ClimateBalanceError(f"Invalid projected weather for {key!r}: {error}") from error
        for day in _date_range(start, end):
            if day not in by_day or by_day[day].gap:
                raise ClimateBalanceError(f"Climate member {key!r} lacks valid weather on {day}.")
        weather[key] = [by_day[day] for day in _date_range(start, end)]
        metadata[key] = {name: value for name, value in series.items() if name != "daily"}
    return metadata, weather


def _plantings(specs, temps, engine):
    plantings = []
    for record, day in specs:
        year_temperatures = {
            dt: pair for dt, pair in temps.items() if day <= dt <= date(day.year, 12, 31)
        }
        planting = engine.crops.Planting(record, day, year_temperatures)
        if not planting.complete:
            raise ClimateBalanceError(
                f"{record.name_en!r} planted on {day} does not complete by year-end."
            )
        if plantings and plantings[-1].end >= day:
            raise ClimateBalanceError(f"Crop planted on {day} overlaps the previous modeled crop.")
        plantings.append(planting)
    return plantings


def _season_result(planting, earlier_planting, next_planting, simulated, capacity, *, strategy):
    sowing, end = planting.start, planting.end
    opening, closing = simulated[sowing - timedelta(days=1)], simulated[end]
    days = [simulated[day] for day in _date_range(sowing, end)]
    metrics = summarize_days(
        [
            BalanceDay(
                date=day.date,
                cover=planting.crop.name_en,
                etc_mm=day.etc_mm,
                eta_mm=day.eta_mm,
                stress=day.stress,
                wet_stress=None,
                irrigation_mm=day.irrigation_mm,
                gap=day.gap,
            )
            for day in days
        ]
    )
    summary = metrics.model_dump()
    if strategy:
        summary["scheduled_irrigation_mm"] = round(fsum(day.irrigation_mm for day in days), 3)
    summary["drought_stress_crop_day_pct"] = round(
        100 * metrics.drought_stress_days / metrics.crop_days, 1
    )
    previous_end = earlier_planting.end if earlier_planting else None
    recharged = None
    if previous_end is not None:
        recharged = next(
            (
                day.isoformat()
                for day in _date_range(previous_end + timedelta(days=1), sowing - timedelta(days=1))
                if simulated[day].profile_mm >= 0.8 * capacity
            ),
            None,
        )
    recharge_end = (
        min(date(end.year, 12, 31), next_planting.start - timedelta(days=1))
        if next_planting
        else date(end.year, 12, 31)
    )
    after_end = next(
        (
            day
            for day in _date_range(end if strategy else end + timedelta(days=1), recharge_end)
            if simulated[day].profile_mm >= 0.8 * capacity
        ),
        None,
    )
    flowering = planting.crop.flowering.value if strategy else None
    flowering_on = (
        next(
            (
                day.isoformat()
                for day in _date_range(sowing, end)
                if planting.thermal(day) >= flowering["gdd_c"]
            ),
            None,
        )
        if flowering and flowering["method"] == "gdd"
        else None
    )
    return {
        "crop_id": planting.crop.name_en,
        "planting_date": sowing.isoformat(),
        "end_date": end.isoformat(),
        **({"modeled_flowering_on": flowering_on} if flowering else {}),
        "end_reason": planting.end_reason,
        "end_date_is_estimated": True,
        "previous_end_date": previous_end.isoformat() if previous_end else None,
        "previous_end_date_is_estimated": True if previous_end else None,
        "fallow_days_before_planting": (sowing - previous_end).days - 1 if previous_end else None,
        "profile_opening_mm": round(opening.profile_mm, 3),
        "profile_closing_mm": round(closing.profile_mm, 3),
        "opening_profile_water_mm": round(opening.profile_mm, 3),
        "closing_profile_water_mm": round(closing.profile_mm, 3),
        "opening_snow_water_mm": round(opening.snow_mm, 3),
        "closing_snow_water_mm": round(closing.snow_mm, 3),
        "recharged_to_80pct_after_previous_end_on": recharged,
        "post_crop_recharge": {
            "target_available_water_pct": 80,
            "target_reached_on": after_end.isoformat() if after_end else None,
            "days_after_modeled_crop_end": (after_end - end).days if after_end else None,
            "searched_through": recharge_end.isoformat(),
            "status": "reached"
            if after_end
            else "not_reached_in_window"
            if recharge_end > end
            else "no_followup_days",
        },
        "metrics": summary,
    }


def _scenario_rows(plantings, wx, layers, soil_source, engine, *, strategy, irrigation_capacity_mm):
    surface, depth, awc, gravity = layers
    hydraulics = engine.soil.SoilHydraulics(surface[2], surface[3], soil_source)
    profile = engine.balance.Profile(
        hydraulics,
        depth_m=depth,
        relative_wetness=INITIAL_AVAILABLE_FRACTION,
        layer_awc=awc,
        layer_gravity=gravity,
        drain=(DRAIN_FRACTION,) * len(awc) if gravity else None,
    )
    # A single run carries all layer water, snow, and residue across every season.
    simulated = {
        day.date: day
        for day in engine.balance.simulate(
            profile,
            wx,
            plantings,
            irrigation_capacity_mm=irrigation_capacity_mm,
        )
    }
    capacity = profile.capacity(depth)
    return [
        _season_result(
            crop,
            plantings[index - 1] if index else None,
            plantings[index + 1] if index + 1 < len(plantings) else None,
            simulated,
            capacity,
            strategy=strategy,
        )
        for index, crop in enumerate(plantings)
    ]


def compare_candidates(
    *,
    latitude,
    longitude,
    soil_source,
    soil_layers,
    historical_weather,
    climate_weather,
    candidates,
    planting_date,
    selected=(),
    ruslan_dir,
    strategy=False,
    irrigation_capacity_mm=None,
):
    """Return modeled one-season outcomes under every shared projected weather member.

    Candidate choices accept a crop ID or a mapping with optional calendar_id or
    stage_lengths. Prior choices must additionally specify their planted date.
    """
    latitude = _number(latitude, "Northern Hemisphere field latitude", 0, 90)
    longitude = _number(longitude, "field longitude", -180, 180)
    if not soil_source:
        raise ClimateBalanceError("A soil source is required alongside the hydraulic parameters.")
    start, end = required_weather_dates(planting_date, selected, strategy=strategy)
    planting_date = _as_date(planting_date)
    candidate_choices = [
        {"crop_id": item} if isinstance(item, str) else item for item in candidates
    ]
    candidate_ids = [choice.get("crop_id") for choice in candidate_choices]
    choice_ids = [choice.get("choice_id", choice.get("crop_id")) for choice in candidate_choices]
    limit = MAX_STRATEGY_CANDIDATES if strategy else 8
    if (
        not 1 <= len(candidate_ids) <= limit
        or not all(isinstance(key, str) and key for key in candidate_ids)
        or not all(isinstance(key, str) and key for key in choice_ids)
        or len(set(choice_ids)) != len(choice_ids)
    ):
        raise ClimateBalanceError(f"Supply one to {limit} unique candidate choices.")
    temperatures, historical = _historical_reference(historical_weather, latitude, longitude)
    if _as_date(historical["end"]) >= start:
        raise ClimateBalanceError(
            "The historical phenology reference must predate the climate simulation."
        )
    catalogue = {profile.id: profile for profile in crop_catalog()}
    with _ENGINE_LOCK:
        engine = _load_engine(ruslan_dir)
        layers = _profile_layers(soil_layers, engine.balance.LAYER_M)
        soil_depth = layers[1]
        metadata, weather = _weather_series(
            climate_weather,
            latitude,
            longitude,
            historical["elevation_m"],
            start,
            end,
            engine,
        )
        member_temperatures = {
            key: {day.date: (day.tmax, day.tmin) for day in wx} for key, wx in weather.items()
        }
        with _local_phenology(engine, temperatures) as hybrid_thresholds:
            prior = []
            prior_profiles = []
            for index, choice in enumerate(selected):
                day = _as_date(choice["planting_date"])
                try:
                    record, reference = _crop_record(
                        engine,
                        catalogue,
                        choice,
                        day,
                        soil_depth,
                        f"previous_{index}",
                        strategy=strategy,
                        hybrid_thresholds=hybrid_thresholds,
                    )
                except ValueError as error:
                    raise ClimateBalanceError(f"Invalid selected crop at {day}: {error}") from error
                prior.append((record, day))
                prior_profiles.append(reference)
            prior_plantings = {
                key: _plantings(prior, temps, engine) for key, temps in member_temperatures.items()
            }
            selected_scenarios = {}
            output = []
            for index, choice in enumerate(candidate_choices):
                crop_id = choice["crop_id"]
                choice_id = choice_ids[index]
                try:
                    record, reference = _crop_record(
                        engine,
                        catalogue,
                        choice,
                        planting_date,
                        soil_depth,
                        f"candidate_{index}",
                        strategy=strategy,
                        hybrid_thresholds=hybrid_thresholds,
                    )
                    scenarios = {}
                    replayed_selected = {}
                    for key, wx in weather.items():
                        candidate = _plantings(
                            [(record, planting_date)], member_temperatures[key], engine
                        )[0]
                        if prior_plantings[key] and prior_plantings[key][-1].end >= planting_date:
                            raise ClimateBalanceError("Sowing overlaps the previous modeled crop.")
                        plantings = [*prior_plantings[key], candidate]
                        results = _scenario_rows(
                            plantings,
                            wx,
                            layers,
                            soil_source,
                            engine,
                            strategy=strategy,
                            irrigation_capacity_mm=irrigation_capacity_mm,
                        )
                        replayed_selected[key], scenarios[key] = results[:-1], results[-1]
                    selected_scenarios = replayed_selected
                    minimum_next = (
                        max(_as_date(row["end_date"]) for row in scenarios.values())
                        + timedelta(days=1)
                        if strategy
                        else date(planting_date.year + 1, 3, 1)
                    )
                    output.append(
                        {
                            "crop_id": crop_id,
                            "choice_id": choice_id,
                            "status": "ok",
                            "reason": None,
                            "scenarios": scenarios,
                            "selected_scenarios": selected_scenarios,
                            "phenology": reference,
                            "min_next_planting_date": minimum_next.isoformat(),
                        }
                    )
                except ValueError as error:
                    output.append(
                        {
                            "crop_id": crop_id,
                            "choice_id": choice_id,
                            "status": "unsupported",
                            "reason": str(error),
                            "scenarios": {},
                        }
                    )
            if not selected_scenarios:
                selected_scenarios = {
                    key: _scenario_rows(
                        prior_plantings[key],
                        wx,
                        layers,
                        soil_source,
                        engine,
                        strategy=strategy,
                        irrigation_capacity_mm=irrigation_capacity_mm,
                    )
                    if prior
                    else []
                    for key, wx in weather.items()
                }
    try:
        ruslan_fingerprint = hashlib.sha256(Path(engine.balance.__file__).read_bytes()).hexdigest()
    except OSError as error:
        raise ClimateBalanceError(
            "Could not read the configured water balance source for provenance."
        ) from error
    return {
        "candidates": output,
        "weather_members": metadata,
        "weather_start": start.isoformat(),
        "weather_end": end.isoformat(),
        "historical_weather": historical,
        "selected_phenology": prior_profiles,
        "selected_scenarios": selected_scenarios,
        "model_source": f"Ruslan {engine.balance.MODEL_VERSION} and {NASA}",
        "ruslan_balance_sha256": ruslan_fingerprint,
        "data_kind": "conditional_scenario",
        "soil_source": soil_source,
        "soil_depth_m": soil_depth,
        "available_water_capacity_mm": round(fsum(layers[2]) * engine.balance.LAYER_M * 1000, 3),
        "assumptions": {
            "initial_available_water_fraction": INITIAL_AVAILABLE_FRACTION,
            "drainage": "10% of water above field capacity per day"
            if layers[3] is not None
            else "Immediate tipping to field capacity; complete saturation missing.",
            "irrigation_mm": 0 if irrigation_capacity_mm is None else None,
            "reference_evapotranspiration": (
                f"FAO-56 Penman–Monteith with daily mean relative humidity. {FAO56}"
            ),
            "crop_coefficients": (
                "Unadjusted FAO-56 values; climate minimum daily humidity unavailable."
            ),
            "profile_storage": (
                "Millimetres above permanent wilting point, across the full "
                "modeled soil profile, including gravity water if present."
            ),
            "planting_scope": (
                "Northern Hemisphere spring/summer crops only, March–July "
                "planting. In Strategies, successive crops must not overlap."
                if strategy
                else "Northern Hemisphere spring/summer crops only, March–July planting, "
                "one crop per calendar year."
            ),
            "crop_end": (
                "Thermal maturity or Ruslan’s generic September-onward killing "
                "frost rule. Dates are estimates."
            ),
            "rotation_scope": (
                "Water, snow and assumed residue carryover only. Crop data are "
                "species-level proxies."
            ),
            "phenology_scope": (
                "Temperature-based development. Photoperiod and cultivar-specific "
                "cold tolerance are outside this model."
            ),
            "recharge_diagnostic": (
                "First fallow day after crop end with at least 80% of the modeled "
                "profile's water capacity."
            ),
            "water_stress": (
                "Dry days are modeled separately. Waterlogging and combined water "
                "stress are unavailable."
            ),
        },
    }
