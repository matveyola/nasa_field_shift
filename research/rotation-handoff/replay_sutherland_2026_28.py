"""Replay Sutherland 2026–2028 or compare history and NASA scenarios for 2027."""

import gzip
import json
from argparse import ArgumentParser
from datetime import date, timedelta
from hashlib import sha256
from math import fsum, isfinite
from pathlib import Path
from statistics import median

from pydantic import BaseModel

from field_shift.modules.rotations import climate_balance as balance
from field_shift.modules.rotations.climate_economics import season_economics
from field_shift.modules.rotations.climate_reference import load_thermal_reference
from field_shift.modules.rotations.historical_fields import load_fields
from field_shift.modules.rotations.hybrid_profiles import crop_catalog
from field_shift.modules.rotations.rotation_agronomy import (
    AgronomySeason,
    assess_rotation_agronomy,
)
from field_shift.modules.rotations.supplied_balance import BalanceDay, summarize_days

EXAMPLE = Path(__file__).resolve().parent / "examples" / "sutherland-2026-28"
START = date(2026, 5, 1)
NASA_START = date(2027, 5, 1)
NASA_END = date(2027, 9, 30)
DEFAULT_NASA_WEIGHT = 0.5
LAST_RAIN_ONLY = date(2026, 9, 30)
ARCHIVE_CUTOFF = date(2026, 11, 7)
END = date(2028, 12, 31)
ANALOG_YEARS = (2019, 2023)
CROPS = ("maize_grain", "soybeans", "sunflower", "wheat_spring")
MOISTURE_FIELDS = tuple(
    f"soil_moisture_{interval}cm_mean" for interval in ("0_to_7", "7_to_28", "28_to_100")
)
WEATHER_FIELDS = ("temperature_2m_min", "temperature_2m_max", "et0_fao_evapotranspiration")


class SutherlandPreviousCrop(BaseModel):
    crop_id: str
    planting_date: date


class SutherlandNasaMember(BaseModel):
    model: str
    scenario: str
    member: str
    grid_latitude: float
    grid_longitude: float


class SutherlandNasaSource(BaseModel):
    dataset: str
    version: str
    source_url: str
    sha256: str
    overlay_start: date
    overlay_end: date
    members: dict[str, SutherlandNasaMember]


class SutherlandEnsemble(BaseModel):
    history_weight: float
    nasa_weight: float
    method: str
    weight_kind: str


class SutherlandCropRun(BaseModel):
    opening_water_mm: float
    season_end: date
    end_reason: str
    modeled_crop_days: int
    water_shortfall_mm: float
    water_shortfall_pct: float
    drought_stress_days: int


class SutherlandCandidate(BaseModel):
    return_interval_status: str
    historical: dict[str, SutherlandCropRun]
    nasa: dict[str, dict[str, SutherlandCropRun]]
    median_historical_water_shortfall_pct: float
    median_water_shortfall_pct_by_nasa_model: dict[str, float]
    median_nasa_water_shortfall_pct: float
    ensemble_score: float


class SutherlandRanking(BaseModel):
    historical: list[str]
    nasa_models: dict[str, list[str]]
    nasa: list[str]
    ensemble: list[str]


class Sutherland2027Result(BaseModel):
    field_id: str
    previous_crop: SutherlandPreviousCrop
    planting_date: date
    historical_analog_years: list[int]
    archive_cutoff: date
    nasa: SutherlandNasaSource
    ensemble: SutherlandEnsemble
    candidates: dict[str, SutherlandCandidate]
    ranking_by_water_shortfall: SutherlandRanking


def run_sutherland_2027(engine_dir: str) -> Sutherland2027Result:
    """Recalculate the bundled scenario using the same engine lock as climate requests."""
    with balance._ENGINE_LOCK:
        result = replay(
            engine_dir, EXAMPLE / "weather.json.gz",
            nasa_path=EXAMPLE / "nasa-2027.json.gz",
            nasa_weight=DEFAULT_NASA_WEIGHT,
        )
    return Sutherland2027Result.model_validate(result)


def calendar_rows(payload):
    daily = payload["daily"]
    columns = tuple(key for key in daily if key != "time")
    rows = {}
    for raw_day, *values in zip(daily["time"], *(daily[key] for key in columns), strict=True):
        day = date.fromisoformat(raw_day)
        if day in rows or any(not isinstance(value, (int, float)) or not isfinite(value) for value in values):
            raise ValueError(f"Duplicate date or invalid archive value on {day}.")
        rows[day] = dict(zip(columns, values, strict=True))
    return rows


def archive_weather(engine, recent, historical, analog):
    rows = []
    for day in balance._date_range(START, END):
        if day <= ARCHIVE_CUTOFF:
            source, values = "ERA5-Seamless archive 2026", recent[day]
        else:
            analog_day = day.replace(year=day.year - 2027 + analog)
            source, values = f"ERA5-Seamless historical analog {analog_day}", historical[analog_day]
        precipitation = "rain_sum" if day <= LAST_RAIN_ONLY else "precipitation_sum"
        low, high, et0 = (values[field] for field in WEATHER_FIELDS)
        rows.append(engine.weather.WeatherDay(day, high, low, values[precipitation], et0, source=source))
    return rows


def opening_profile(engine, field, layers, capacity, theta):
    surface, depth, available, _ = capacity
    hydraulics = engine.soil.SoilHydraulics(surface[2], surface[3], field.soil_source_url)
    profile = engine.balance.Profile(hydraulics, depth_m=depth, layer_awc=available)
    moisture = tuple(zip((0, .07, .28), (.07, .28, 1), theta, strict=True))
    for index, cap in enumerate(profile.caps):
        bottom, top = (index + 1) * engine.balance.LAYER_M, index * engine.balance.LAYER_M
        # Integrate the reanalysis water content minus wilting point in each
        # intersecting sublayer. Clamp only after combining the intersections.
        water = fsum(
            max(0, min(bottom, moi_bottom, layer["bottom_m"]) - max(top, moi_top, layer["top_m"]))
            * (value - layer["wilting_point"]) * 1000
            for moi_top, moi_bottom, value in moisture for layer in layers
        )
        profile.w[index] = min(cap, max(0, water))
    return profile


def summarize_crop(planting, crop, sowing, next_sowing, days, opening, recharge_threshold):
    metrics = summarize_days([
        BalanceDay(date=x.date, cover=crop, etc_mm=x.etc_mm, eta_mm=x.eta_mm,
                   stress=x.stress, irrigation_mm=0, gap=False)
        for x in days.values() if x.cover == planting.crop.key
    ]).model_dump()
    gap_end = next_sowing - timedelta(days=1) if next_sowing else date(sowing.year, 12, 31)
    reached = next((
        x.date for day, x in days.items() if planting.end < day <= gap_end and x.profile_mm >= recharge_threshold
    ), None)
    closing = days[planting.end].profile_mm
    if closing >= recharge_threshold:
        recharge_days = 0
    else:
        recharge_days = (reached - planting.end).days if reached else None
    return {
        "crop": crop, "planting": sowing, "end": planting.end, "end_reason": planting.end_reason,
        "metrics": metrics, "opening": opening, "closing": closing, "recharge": reached,
        "recharge_days": recharge_days,
        "gap_days": (next_sowing - planting.end).days - 1 if next_sowing else None,
        "year_end": days[date(sowing.year, 12, 31)].profile_mm,
    }


def compare_2027_scenarios(nasa_path, nasa_weight, field, engine, elevation, weather, historical, simulate):
    if not 0 <= nasa_weight <= 1:
        raise ValueError("NASA scenario weight must be between zero and one.")
    raw = nasa_path.read_bytes()
    payload = json.loads(gzip.decompress(raw))
    metadata, nasa_weather = balance._weather_series(
        payload, field.latitude, field.longitude, elevation, NASA_START, NASA_END, engine,
    )
    patched = {}
    for member, projections in nasa_weather.items():
        patch = {row.date: row for row in projections}
        patched[member] = {
            analog: [patch.get(row.date, row) for row in rows] for analog, rows in weather.items()
        }

    def crop_summary(run):
        return {
            "opening_water_mm": run["opening"],
            "season_end": run["end"],
            "end_reason": run["end_reason"],
            "modeled_crop_days": run["metrics"]["crop_days"],
            "water_shortfall_mm": run["metrics"]["deficit_mm"],
            "water_shortfall_pct": run["metrics"]["water_shortfall_pct"],
            "drought_stress_days": run["metrics"]["drought_stress_days"],
        }

    candidates = {}
    for crop, reference in historical.items():
        sowings = [("maize_grain", START), (crop, NASA_START)]
        analogs = {year: crop_summary(path[-1]) for year, path in reference["analogs"].items()}
        projections = {
            member: {str(year): crop_summary(simulate(sowings, rows)[-1]) for year, rows in members.items()}
            for member, members in patched.items()
        }
        if any(run["season_end"] > NASA_END for rows in projections.values() for run in rows.values()):
            raise ValueError(f"NASA projection ends before the modeled season for {crop}.")

        historical_deficit = median(run["water_shortfall_pct"] for run in analogs.values())
        per_model_deficit = {
            member: median(run["water_shortfall_pct"] for run in rows.values())
            for member, rows in projections.items()
        }
        nasa_deficit = median(per_model_deficit.values())
        candidates[crop] = {
            "return_interval_status": reference["rule"],
            "historical": analogs,
            "nasa": projections,
            "median_historical_water_shortfall_pct": historical_deficit,
            "median_water_shortfall_pct_by_nasa_model": per_model_deficit,
            "median_nasa_water_shortfall_pct": nasa_deficit,
            "ensemble_score": round((1 - nasa_weight) * historical_deficit + nasa_weight * nasa_deficit, 6),
        }

    eligible = [crop for crop in candidates if candidates[crop]["return_interval_status"] != "below_reference_interval"]

    return {
        "field_id": "sutherland_nw",
        "previous_crop": {"crop_id": "maize_grain", "planting_date": START},
        "planting_date": NASA_START,
        "historical_analog_years": list(ANALOG_YEARS),
        "archive_cutoff": ARCHIVE_CUTOFF,
        "nasa": {
            "dataset": payload["dataset"],
            "version": payload["version"],
            "source_url": payload["dataset_url"],
            "sha256": sha256(raw).hexdigest(),
            "overlay_start": NASA_START,
            "overlay_end": NASA_END,
            "members": {
                member: {key: row[key] for key in ("model", "scenario", "member", "grid_latitude", "grid_longitude")}
                for member, row in metadata.items()
            },
        },
        "ensemble": {
            "history_weight": round(1 - nasa_weight, 6),
            "nasa_weight": nasa_weight,
            "method": "Weighted sum of group medians after simulating each weather sequence separately.",
            "weight_kind": "planning_setting",
        },
        "candidates": candidates,
        "ranking_by_water_shortfall": {
            "historical": sorted(eligible, key=lambda crop: candidates[crop]["median_historical_water_shortfall_pct"]),
            "nasa_models": {
                member: sorted(eligible, key=lambda crop: candidates[crop]["median_water_shortfall_pct_by_nasa_model"][member])
                for member in metadata
            },
            "nasa": sorted(eligible, key=lambda crop: candidates[crop]["median_nasa_water_shortfall_pct"]),
            "ensemble": sorted(eligible, key=lambda crop: candidates[crop]["ensemble_score"]),
        },
    }


def replay(engine_dir, weather_path, *, nasa_path=None, nasa_weight=DEFAULT_NASA_WEIGHT):
    raw = weather_path.read_bytes()
    weather_input = json.loads(gzip.decompress(raw))
    recent = calendar_rows(weather_input["observed_2026"])
    historical = calendar_rows(weather_input["historical_analogs"])
    field = load_fields()["sutherland_nw"]
    engine = balance._load_engine(engine_dir)
    layers = [layer.model_dump() for layer in field.soil_layers]
    capacity = balance._profile_layers(layers, engine.balance.LAYER_M)
    capacity_mm = fsum(capacity[2]) * engine.balance.LAYER_M * 1000
    temperature, reference = balance._historical_reference(
        load_thermal_reference(field.latitude, field.longitude), field.latitude, field.longitude,
    )
    catalogue = {p.id: p for p in crop_catalog()}
    theta = tuple(recent[START - timedelta(days=1)][name] for name in MOISTURE_FIELDS)
    former = field.seasons[2026]
    if theta != former.initial_theta or any(
        (recent[row.date]["rain_sum"], *(recent[row.date][name] for name in WEATHER_FIELDS))
        != (row.rain_mm, row.low_c, row.high_c, row.reference_et0_mm) for row in former.days
    ):
        raise ValueError("The weather input does not reproduce the bundled 2026 field baseline.")
    weather = {analog: archive_weather(engine, recent, historical, analog) for analog in ANALOG_YEARS}
    reference_finance = {
        crop: season_economics(crop, None, country_code="USA", marsden=False, region_code="US-IA") for crop in CROPS
    }

    def simulate(sowings, weather_rows):
        temperatures = {row.date: (row.tmax, row.tmin) for row in weather_rows}
        profile = opening_profile(engine, field, layers, capacity, theta)
        initial = profile.total()
        with balance._local_phenology(engine, temperature):
            records = [
                (balance._crop_record(engine, catalogue, {"crop_id": crop}, sowing, capacity[1], f"p{index}")[0], sowing)
                for index, (crop, sowing) in enumerate(sowings)
            ]
            plantings = balance._plantings(records, temperatures, engine)
            days = {row.date: row for row in engine.balance.simulate(profile, weather_rows, plantings)}
        return [
            summarize_crop(
                planting, crop, sowing, sowings[index + 1][1] if index + 1 < len(sowings) else None,
                days, initial if sowing == START else days[sowing - timedelta(days=1)].profile_mm,
                0.8 * capacity_mm,
            ) for index, (planting, (crop, sowing)) in enumerate(zip(plantings, sowings, strict=True))
        ]

    def compare(previous, year):
        previous = [(crop, date(previous_year, 5, 1)) for crop, previous_year in previous]
        history = [AgronomySeason(season_year=day.year, crop_id=crop, planting_date=day) for crop, day in previous]
        sowing = date(year, 5, 1)
        results = {}
        for crop in CROPS:
            candidate = AgronomySeason(season_year=year, crop_id=crop, planting_date=sowing)
            agronomy = assess_rotation_agronomy(history, candidate=candidate, region_code="US-IA", country_code="USA")
            analogs = ANALOG_YEARS if year > 2026 else ANALOG_YEARS[:1]
            results[crop] = {
                "analogs": {str(analog): simulate([*previous, (crop, sowing)], weather[analog]) for analog in analogs},
                "rule": agronomy.candidate.disease_interval_status,
                "finance": reference_finance[crop],
            }
        return results

    maize = ("maize_grain", 2026)
    after_maize = compare([maize], 2027)
    if nasa_path is not None:
        return compare_2027_scenarios(
            nasa_path, nasa_weight, field, engine, reference["elevation_m"], weather, after_maize, simulate,
        )
    branches = {
        "2026": compare([], 2026),
        "2027 after maize": after_maize,
        "2027 after sunflower": compare([("sunflower", 2026)], 2027),
    }
    for crop in ("sunflower", "soybeans", "maize_grain", "wheat_spring"):
        branches["2028 after maize then " + crop] = compare([maize, (crop, 2027)], 2028)

    rankings = {}
    eligible_rankings = {}
    for label, candidates in branches.items():
        signals = [
            {"crop_id": crop, "median_water_shortfall_pct": round(median(
                path[-1]["metrics"]["water_shortfall_pct"] for path in candidate["analogs"].values()
            ), 2)} for crop, candidate in candidates.items()
        ]
        rankings[label] = sorted(signals, key=lambda item: item["median_water_shortfall_pct"])
        eligible_rankings[label] = [
            item for item in rankings[label]
            if candidates[item["crop_id"]]["rule"] != "below_reference_interval"
        ][:3]

    return {
        "data_kind": "offline_simulation_with_historical_analogs",
        "field_id": "sutherland_nw", "latitude": field.latitude, "longitude": field.longitude,
        "sowing_month_day": "05-01", "weather_data": {
            "provider": weather_input["provider"], "sha256": sha256(raw).hexdigest(),
            "era5_archive_2026": {key: value for key, value in weather_input["observed_2026"].items() if key != "daily"},
            "historical_analogs": {key: value for key, value in weather_input["historical_analogs"].items() if key != "daily"},
            "archive_cutoff": ARCHIVE_CUTOFF, "last_rain_sum_date": LAST_RAIN_ONLY,
            "analog_start_years": list(ANALOG_YEARS),
            "analog_mapping": "Source year = target year minus 2027 plus analog start year. Calendar month/day unchanged.",
        },
        "soil": {"source_url": field.soil_source_url, "layers": layers,
                 "initial_water_source_date": START - timedelta(days=1),
                 "initial_wetness_m3_m3": dict(zip(MOISTURE_FIELDS, theta, strict=True)),
                 "initial_available_water_mm": opening_profile(engine, field, layers, capacity, theta).total(),
                 "available_capacity_mm": capacity_mm, "recharge_threshold_mm": 0.8 * capacity_mm},
        "phenology_reference": reference,
        "engine_file_sha256": {
            name: sha256(Path(getattr(engine, name).__file__).read_bytes()).hexdigest()
            for name in ("balance", "crops", "meteo", "soil", "weather")
        },
        "spring_2026_soil_temperature_c": {day.isoformat(): recent[day]["soil_temperature_0_to_7cm_mean"]
                                           for day in balance._date_range(date(2026, 4, 15), date(2026, 5, 7))},
        "branches": branches, "rank_by_median_water_shortfall": rankings,
        "top_eligible_by_water_shortfall": eligible_rankings,
    }


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--ruslan-dir", required=True, type=Path, help="Directory containing Ruslan's fstasks package")
    parser.add_argument("--weather", type=Path, default=EXAMPLE / "weather.json.gz")
    parser.add_argument("--nasa-2027", action="store_true", help="Compare the 2027 historical and NASA scenarios")
    parser.add_argument(
        "--nasa-weight", type=float, default=DEFAULT_NASA_WEIGHT,
        help="NASA group weight between 0 and 1 for the 2027 score (default: %(default)s)",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.nasa_2027 and not 0 <= args.nasa_weight <= 1:
        parser.error("--nasa-weight must be between zero and one")
    output = args.output or EXAMPLE / ("result-nasa-2027.json" if args.nasa_2027 else "result.json")
    result = replay(
        args.ruslan_dir, args.weather,
        nasa_path=EXAMPLE / "nasa-2027.json.gz" if args.nasa_2027 else None,
        nasa_weight=args.nasa_weight,
    )
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False, default=str) + "\n", encoding="utf-8")
    if args.nasa_2027:
        for label, ranking in result["ranking_by_water_shortfall"].items():
            if label == "nasa_models":
                for model, crops in ranking.items():
                    print(f"{model} → {', '.join(crops)}")
            else:
                print(f"{label} → {', '.join(ranking)}")
        print(f"Report saved to {output}")
        return
    for label, ranks in result["rank_by_median_water_shortfall"].items():
        print(label)
        for title, rows in (
            ("All crops by modeled water shortfall", ranks),
            ("Top 3 after checking documented rotation intervals", result["top_eligible_by_water_shortfall"][label]),
        ):
            print(f"  {title} →", ", ".join(f"{row['crop_id']} {row['median_water_shortfall_pct']:g}%" for row in rows))
    print(f"Report saved to {output}")


if __name__ == "__main__":
    main()
