"""Replay NEX-GDDP-CMIP6 daily scenarios through Ruslan's field water balance."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, timedelta
from math import isfinite, log
from pathlib import Path
from statistics import mean, median

from field_shift.modules.rotations.climate_balance import engine_directory
from field_shift.modules.rotations.supplied_balance import (
    BalanceDay,
    CompareSuppliedBalance,
    compare,
    summarize_days,
)

HERE = Path(__file__).resolve().parent
SOIL_EXAMPLE = HERE / "examples/marsden-ssurgo-water-stress.json"
CROP_PROFILES = HERE / "field_shift/modules/rotations/artifacts/forecast-crops.json"
WINDOWS = (2040, 2042, 2044, 2046, 2048)
SOWING = (5, 1)
STARTING_WETNESS = 0.5
DAILY_DRAIN_FRACTION = 0.10
PLANS = {
    "maize-then-sunflower": ("maize", "sunflower"),
    "sunflower-then-maize": ("sunflower", "maize"),
}
COVER_IDS = {"maize": "maize_grain", "sunflower": "sunflower", "fallow": None}
MODEL_SOURCE = (
    "Ruslan fstasks layered bucket, with Marsden SSURGO top 1 m, FAO-56 Penman-Monteith, "
    "shared local thermal reference, 50% available water on October 1 before the full run, "
    "10% daily gravity-water drainage, fixed May 1 planting, and no irrigation."
)
NEX = "NASA NEX-GDDP-CMIP6 (https://doi.org/10.7917/OFSG3345)"
FAO56 = "https://www.fao.org/4/X0490E/X0490E00.htm"


def read_json(path):
    path = Path(path)
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            return json.load(stream)
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value, compact=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoding = {"separators": (",", ":")} if compact else {"indent": 2}
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, **encoding) + "\n", encoding="utf-8")


def dates(start, end):
    return (start + timedelta(days=index) for index in range((end - start).days + 1))


def number(value, name, lower, upper):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or not lower <= value <= upper
    ):
        raise ValueError(f"Invalid {name}: {value!r}; expected [{lower}, {upper}].")
    return value


def historical_reference(path, field):
    """A 31-day moving mean of daily Tmax/Tmin on a common calendar for both crops."""
    payload = read_json(path)
    longitude, latitude, elevation = (payload[name] for name in ("longitude", "latitude", "elevation_m"))
    if abs(latitude - field["latitude"]) > 0.02 or abs(longitude - field["longitude"]) > 0.02:
        raise ValueError("NASA POWER phenology reference is not for Marsden.")
    number(elevation, "site elevation (m)", -100, 4000)
    start, end = (date.fromisoformat(payload[name]) for name in ("start", "end"))
    if start != date(start.year, 1, 1) or end != date(end.year, 12, 31):
        raise ValueError("The phenology reference must contain whole calendar years.")
    daily = {date.fromisoformat(row["date"]): row for row in payload["daily"]}
    if len(daily) != len(payload["daily"]) or len(daily) != (end - start).days + 1:
        raise ValueError("Phenology reference needs one row per day.")
    by_day = defaultdict(list)
    for day in dates(start, end):
        maximum = number(daily[day]["tmax_c"], f"POWER Tmax {day}", -70, 65)
        minimum = number(daily[day]["tmin_c"], f"POWER Tmin {day}", -70, maximum)
        if (day.month, day.day) != (2, 29):
            by_day[day.month, day.day].append((maximum, minimum))
    calendar = [(day.month, day.day) for day in dates(date(2001, 1, 1), date(2001, 12, 31))]
    table = {}
    for index, key in enumerate(calendar):
        neighborhood = [
            sample
            for offset in range(-15, 16)
            for sample in by_day[calendar[(index + offset) % len(calendar)]]
        ]
        table[key] = tuple(mean(values) for values in zip(*neighborhood))
    table[2, 29] = tuple(mean(values) for values in zip(table[2, 28], table[3, 1]))
    return table, {
        "source": payload["source"],
        "source_url": payload["source_url"],
        "request_url": payload["request_url"],
        "start": start.isoformat(),
        "end": end.isoformat(),
        "method": "31-day circular moving mean of the historical grid temperatures, shared across all scenarios",
        "elevation_m": elevation,
        "elevation_source": "NASA POWER point metadata, geometry.coordinates[2]",
        "limitation": "Only the two complete Marsden years already available in the project; not climatological normals.",
    }


def make_profile(balance, soil, marsden, layers):
    observed = marsden["field"]["soil_layers"]
    saturated = marsden["waterlogging_soil"]["layers"]
    horizons = {}
    for horizon in saturated:
        key = (horizon["top_m"], horizon["bottom_m"])
        horizons[key] = horizon["saturation"]
    depth = observed[-1]["bottom_m"]
    awc, gravity = [], []
    for index in range(round(depth / balance.LAYER_M)):
        lower = index * balance.LAYER_M
        upper = lower + balance.LAYER_M
        overlap = 0.0
        available = 0.0
        excess = 0.0
        for layer in observed:
            top, bottom = layer["top_m"], layer["bottom_m"]
            amount = max(0.0, min(bottom, upper) - max(top, lower))
            overlap += amount
            available += amount * (layer["field_capacity"] - layer["wilting_point"])
            excess += amount * (horizons[top, bottom] - layer["field_capacity"])
        if abs(overlap - balance.LAYER_M) > 1e-8 or min(available, excess) <= 0:
            raise ValueError(f"Invalid or uncovered soil layer {lower:g}–{upper:g} m.")
        awc.append(available / balance.LAYER_M)
        gravity.append(excess / balance.LAYER_M)
    hyd = soil.SoilHydraulics(observed[0]["field_capacity"], observed[0]["wilting_point"], layers)
    return balance.Profile(
        hyd, depth_m=depth, relative_wetness=STARTING_WETNESS, layer_awc=awc,
        layer_gravity=gravity, drain=(DAILY_DRAIN_FRACTION,) * len(awc),
    )


@contextmanager
def configured_crops(crops, weather, temperatures, maximum_root_depth):
    base = crops.CROPS["maize"]
    sunflower_before = crops.CROPS.get("sunflower")
    original_normals = weather._NORMALS
    value = crops.Val
    profile = next(row for row in read_json(CROP_PROFILES) if row["id"] == "sunflower")
    calendar = next(row for row in profile["calendars"] if row["id"] == profile["default_calendar_id"])
    ini, mid, end = profile["coefficients"]
    stem_source = "https://www.fao.org/4/X0490E/x0490e0b.htm, table 12"
    root_source = "https://www.fao.org/4/X0490E/x0490e0e.htm, table 22"
    demo = "Marsden climate demonstration assumption"
    skip = value(None, "Not used in this water balance comparison")
    planting = value(SOWING, demo, "assumption")
    sunflower = crops.CropRecord(
        key="sunflower", name_en="Sunflower", name_uk=profile["name"],
        group=value("oilseed", "Handoff forecast-crops profile"),
        region=value("Marsden, Iowa; FAO stage lengths transferred from Medit./California", demo, "adapted"),
        kc_ini=value(ini, stem_source, "adapted", "Oil crops group initial coefficient"),
        kc_mid=value(mid, stem_source, "adapted", "Handoff choice for dense sunflower stand"),
        kc_end=value(end, stem_source),
        height_m=value(2.0, stem_source),
        stages_days=value(tuple(calendar["stage_lengths"]), "https://www.fao.org/4/X0490E/x0490e0b.htm, table 11, profile C11"),
        root_min_m=value(0.15, crops.FAO56_CH8),
        root_max_m=value(profile["root_depth_m"], root_source, "adapted", "Lower bound of published range"),
        p=value(profile["depletion_fraction"], root_source),
        ky=skip, planting=planting, planting_range=value(("05-01", "05-01"), demo, "assumption"),
        end_event=value("harvest", demo, "assumption"),
        flowering=skip, sensitive_phase=skip, heat_threshold_c=skip,
        legume=value(False, "Botany"), cover_class=value("row", crops.TR55, "adapted"),
        winter=value(False, "Botany"),
        gdd_base_c=value(profile["flowering"]["base_c"], profile["flowering"]["source_url"]),
        gdd_cap_c=value(profile["flowering"]["upper_c"], profile["flowering"]["source_url"]),
    )
    crops.CROPS["sunflower"] = sunflower
    crops.CROPS["maize"] = replace(
        base, planting=planting,
        root_max_m=value(min(base.root_max_m.value, maximum_root_depth), "SSURGO modeled depth", "adapted"),
    )
    weather.set_normals(lambda day: temperatures[day.month, day.day])
    crops.reset_calibration()
    try:
        yield
    finally:
        crops.CROPS["maize"] = base
        if sunflower_before is None:
            del crops.CROPS["sunflower"]
        else:
            crops.CROPS["sunflower"] = sunflower_before
        weather.set_normals(original_normals)
        crops.reset_calibration()


def projected_weather(raw, latitude, elevation, meteo, weather):
    label = f"{raw['model']} / {raw['scenario']}"
    available = {}
    for row in raw["daily"]:
        day = date.fromisoformat(row["date"])
        if day in available:
            raise ValueError(f"Duplicate date {day} in {label}.")
        tmax = number(row["tmax_c"], f"Tmax {label} {day}", -70, 65)
        tmin = number(row["tmin_c"], f"Tmin {label} {day}", -70, tmax)
        rain = number(row["rain_mm"], f"precipitation {label} {day}", 0, 1000)
        solar = number(row["rs_mj_m2"], f"radiation {label} {day}", 0, 45)
        rh = number(row["rh_mean_pct"], f"mean relative humidity {label} {day}", 0, 100)
        u10 = number(row["wind_10m_m_s"], f"10 m wind {label} {day}", 0, 100)
        u2 = u10 * 4.87 / log(67.8 * 10 - 5.42)
        et0 = meteo.eto_daily(tmax, tmin, solar, u2, latitude, day.timetuple().tm_yday, elevation, rhmean=rh)
        available[day] = weather.WeatherDay(
            day, tmax, tmin, rain, et0, u2=u2, rs_mj=solar, source=f"{NEX}, {label}",
            rhmean=rh, gap=row.get("gap", False),
        )
    return available


def simulate_plan(sequence, start, end, wx, marsden, balance, crops, soil):
    temps = {day.date: (day.tmax, day.tmin) for day in wx}
    plantings = [
        crops.Planting(crops.CROPS[sequence[offset % len(sequence)]], date(year, *SOWING), temps)
        for offset, year in enumerate(range(start.year, end.year + 1))
    ]
    if any(not planting.complete or planting.end.year != planting.start.year for planting in plantings):
        raise ValueError(f"A crop does not complete within its {start.year}–{end.year} annual sequence.")
    profile = make_profile(balance, soil, marsden, marsden["waterlogging_soil"]["source"])
    simulated = balance.simulate(profile, wx, plantings, irrigation={})
    rows = [
        {
            "date": day.date.isoformat(), "cover": COVER_IDS[day.cover],
            "etc_mm": day.etc_mm, "eta_mm": day.eta_mm, "stress": day.stress,
            "wet_stress": None, "irrigation_mm": day.irrigation_mm, "gap": day.gap,
        }
        for day in simulated if day.date >= start
    ]
    seasons = [
        {
            "crop": COVER_IDS[planting.crop.key],
            "planting": planting.start.isoformat(),
            "end": planting.end.isoformat(),
            "end_reason": planting.end_reason,
        }
        for planting in plantings
    ]
    return rows, seasons


def output_metrics(metrics):
    out = metrics.model_dump()
    out["drought_stress_crop_day_pct"] = (
        round(100 * metrics.drought_stress_days / metrics.crop_days, 1) if metrics.crop_days else None
    )
    return out


def summarize_windows(windows, scenarios):
    summary = {}
    for member in scenarios:
        plans = {}
        for plan in PLANS:
            rows = [window["scenarios"][member][plan]["metrics"] for window in windows]
            plans[plan] = {
                name: {
                    "median": round(median(d[name] for d in rows), 3),
                    "range": [min(d[name] for d in rows), max(d[name] for d in rows)],
                }
                for name in ("deficit_mm", "water_shortfall_pct", "drought_stress_days", "drought_stress_crop_day_pct")
            }
        paired = {}
        first, second = PLANS
        for metric in ("deficit_mm", "water_shortfall_pct", "drought_stress_days", "drought_stress_crop_day_pct"):
            differences = [
                window["scenarios"][member][first]["metrics"][metric]
                - window["scenarios"][member][second]["metrics"][metric]
                for window in windows
            ]
            paired[metric] = {
                "median_first_minus_second": round(median(differences), 3),
                "first_lower_windows": sum(diff < -1e-7 for diff in differences),
                "second_lower_windows": sum(diff > 1e-7 for diff in differences),
                "tied_windows": sum(abs(diff) <= 1e-7 for diff in differences),
            }
        summary[member] = {"plans": plans, "paired_comparison": paired}
    return summary


def run(args, balance, crops, meteo, soil, weather):
    marsden = read_json(SOIL_EXAMPLE)
    field = marsden["field"]
    temperatures, reference = historical_reference(args.calendar_weather, field)
    inputs = read_json(args.weather)
    latitude, longitude = inputs["latitude"], inputs["longitude"]
    if abs(latitude - field["latitude"]) > 0.02 or abs(longitude - field["longitude"]) > 0.02:
        raise ValueError("The projection request coordinates are not for Marsden.")
    scenario_descriptors = {}
    projection = {}
    for series in inputs["series"]:
        model, scenario, member = (series[name] for name in ("model", "scenario", "member"))
        key = f"{model}__{scenario}__{member}"
        if key in projection:
            raise ValueError(f"Duplicate climate member {key}")
        scenario_descriptors[key] = {name: value for name, value in series.items() if name != "daily"}
        projection[key] = projected_weather(series, latitude, reference["elevation_m"], meteo, weather)
    if not projection:
        raise ValueError("No projected weather series supplied.")
    windows = []
    start = date(args.start_years[0], 1, 1)
    end = date(args.start_years[-1] + 1, 12, 31)
    spin_up = date(start.year - 1, 10, 1)
    simulations = {plan: {} for plan in PLANS}
    lifecycle = {member: {} for member in projection}
    period_totals = {member: {} for member in projection}
    depth = field["soil_layers"][-1]["bottom_m"]
    with configured_crops(crops, weather, temperatures, depth):
        crop_config = {
            key: {
                name: {
                    "value": getattr(crops.CROPS[key], name).value,
                    "source": getattr(crops.CROPS[key], name).source,
                    "status": getattr(crops.CROPS[key], name).status,
                    "note": getattr(crops.CROPS[key], name).note,
                }
                for name in (
                    "kc_ini", "kc_mid", "kc_end", "stages_days", "root_min_m", "root_max_m",
                    "p", "gdd_base_c", "gdd_cap_c", "residue_cover",
                )
            } | {"calibrated_thermal_stage_ends_c_days": crops.thermal_stages(key)}
            for key in COVER_IDS if key != "fallow"
        }
        for member, available in projection.items():
            wx = []
            for day in dates(spin_up, end):
                if day not in available:
                    raise ValueError(f"{member} lacks {day}, required for the continuous simulation.")
                wx.append(available[day])
            for plan, sequence in PLANS.items():
                rows, seasons = simulate_plan(sequence, start, end, wx, marsden, balance, crops, soil)
                simulations[plan][member] = [BalanceDay.model_validate(row) for row in rows]
                lifecycle[member][plan] = seasons
                period_totals[member][plan] = output_metrics(summarize_days(simulations[plan][member]))
        for year in args.start_years:
            window_start, window_end = date(year, 1, 1), date(year + 1, 12, 31)
            first, last = (window_start - start).days, (window_end - start).days + 1
            breakdown = {member: {} for member in projection}
            request = CompareSuppliedBalance.model_validate({
                "field_id": "marsden_demo", "start": window_start, "end": window_end, "model_source": MODEL_SOURCE,
                "scenarios": {
                    member: {"data_kind": "conditional_scenario", "weather_source": f"{NEX}; {member}"}
                    for member in projection
                },
                "plans": [
                    {"id": plan, "members": {member: rows[first:last] for member, rows in members.items()}}
                    for plan, members in simulations.items()
                ],
            })
            comparison = compare(request)
            for plan in comparison.plans:
                request_plan = next(row for row in request.plans if row.id == plan.id)
                for member, metrics in plan.scenarios.items():
                    seasons = [
                        season for season in lifecycle[member][plan.id]
                        if window_start <= date.fromisoformat(season["planting"]) <= window_end
                    ]
                    row = {"seasons": seasons}
                    breakdown[member][plan.id] = row
                    row["metrics"] = output_metrics(metrics)
                    for season in row["seasons"]:
                        season_start, season_end = (date.fromisoformat(season[name]) for name in ("planting", "end"))
                        crop_rows = [
                            day for day in request_plan.members[member]
                            if day.cover == season["crop"] and season_start <= day.date <= season_end
                        ]
                        season["metrics"] = output_metrics(summarize_days(crop_rows))
            windows.append({
                "start": window_start.isoformat(), "end": window_end.isoformat(),
                "scenarios": breakdown, "water_stress_comparable": comparison.water_stress_comparable,
            })
            if args.example_request and year == args.start_years[0]:
                example = request.model_dump(mode="json", exclude_none=False)
    output = {
        "field": {"id": "marsden_demo", "latitude": latitude, "longitude": longitude},
        "data_kind": "conditional_scenario",
        "weather_dataset": NEX,
        "all_weather_metadata": {key: value for key, value in inputs.items() if key != "series"},
        "scenarios": scenario_descriptors,
        "model_source": MODEL_SOURCE,
        "ruslan_model_version": balance.MODEL_VERSION,
        "ruslan_balance_sha256": hashlib.sha256(Path(balance.__file__).read_bytes()).hexdigest(),
        "phenology_reference": reference,
        "crops": crop_config,
        "soil_source": marsden["waterlogging_soil"]["source"],
        "available_water_capacity_mm": round(make_profile(balance, soil, marsden, "SSURGO").capacity(depth), 3),
        "comparison_period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "spin_up_from": spin_up.isoformat(),
        },
        "period_totals": period_totals,
        "assumptions": {
            "fixed_planting_month_day": "05-01",
            "soil_depth_m": depth,
            "initial_available_water_fraction": STARTING_WETNESS,
            "daily_gravity_drain_fraction": DAILY_DRAIN_FRACTION,
            "irrigation_mm": 0,
            "rain_multiplier": 1,
            "reference_et": "FAO-56 Penman-Monteith, with daily mean relative humidity and wind adjusted from 10 m to 2 m.",
            "reference_et_source": FAO56,
            "kc_climate_adjustment": "Table coefficients; daily minimum relative humidity is unavailable.",
            "sunflower_calendar": "FAO-56 Table 11 Mediterranean/California stage lengths transferred to Marsden.",
            "rotation_effects": "Modeled water and snow carry-over without annual resets, crop-specific growth and demand.",
        },
        "definitions": {
            "deficit_mm": "Sum of potential minus actual crop water use, cropped days only.",
            "water_shortfall_pct": "100 times deficit divided by modeled potential water use.",
            "drought_stress_days": "Ruslan daily closing root-zone storage below its modeled stress threshold, after establishment.",
            "drought_stress_crop_day_pct": "100 times drought stress days divided by all crop days.",
            "water_stress_days": "Unavailable because waterlogging stress is not modeled.",
        },
        "windows": windows,
        "summary": summarize_windows(windows, projection),
    }
    write_json(args.output, output)
    if args.example_request:
        write_json(args.example_request, example, compact=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ruslan-dir", type=Path, default=engine_directory(),
        help="Override the bundled Ruslan engine directory.",
    )
    parser.add_argument("--weather", type=Path, required=True, help="Normalized NASA NEX-GDDP-CMIP6 weather JSON.")
    parser.add_argument("--calendar-weather", type=Path, required=True, help="Compact historical Marsden NASA POWER daily JSON.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--example-request", type=Path, help="Write the first window as a supplied balance API request.")
    parser.add_argument("--start-years", nargs="+", type=int, choices=WINDOWS, default=WINDOWS)
    args = parser.parse_args()
    args.start_years = sorted(set(args.start_years))
    if args.start_years != list(range(args.start_years[0], args.start_years[-1] + 1, 2)):
        parser.error("--start-years must form one continuous sequence of two-year windows")
    source = args.ruslan_dir.resolve()
    if not (source / "fstasks/balance.py").is_file():
        parser.error("--ruslan-dir must contain fstasks/balance.py")
    sys.path.insert(0, str(source))
    from fstasks import balance, crops, meteo, soil, weather

    try:
        run(args, balance, crops, meteo, soil, weather)
    except (KeyError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
