"""Paired equation replay on public weather; agreement is explicitly not accuracy."""

import argparse
import gzip
import hashlib
import json
from dataclasses import asdict, replace
from datetime import date, timedelta
from importlib.resources import files
from math import fsum, sqrt
from pathlib import Path

from .modules.rotations import climate_balance
from .modules.rotations.historical_fields import load_fields
from .modules.rotations.hybrid_profiles import crop_catalog
from .runtime import ENGINE_VERSION, SOURCE_COMMIT, calculate

PROTOCOL = {
    "id": "rotation-handoff-paired-v1",
    "upstream_commit": SOURCE_COMMIT,
    "fields": ["crawfordsville_se", "marsden_demo", "sutherland_nw"],
    "years": "earliest and latest complete bundled May–September archive year",
    "crop_selection": (
        "every exact catalog profile having a published calendar of at "
        "most153days; first such reference calendar"
    ),
    "horizon": (
        "153 observed archive days plus complete NASA conditional climate 1/2/3-year replays"
    ),
    "climate_cohort": "All six bundled NASA members at two Iowa cells, four crops, 1/2/3 years",
    "opening_available_fraction": 0.5,
    "opening_basis": "controlled assumption shared by both equations, not a measured field state",
    "soil": "same supplied bundled hydraulic layers projected onto native10cm layers",
    "calendars": (
        "same explicit FAO reference phase lengths/coefficients/root "
        "curve for both equations; not locally validated dates"
    ),
    "baseline": (
        "existing field_shift_science.layered_water.balance with all assumptions explicitly matched"
    ),
    "candidate": "installed unchanged Ruslan handoff balance equations",
    "metrics": [
        "paired daily storage/ET/runoff/drainage/demand MAE/RMSE/max delta",
        "daily soil+snow mass closure",
        "stress classification disagreement",
    ],
    "agreement_tolerance_mm": 1e-8,
    "mass_closure_tolerance_mm": 1e-6,
    "empirical_status": "blocked_missing_same_target_observations",
    "promotion": False,
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def mass_error(rows, opening):
    prior, snow = opening, 0.0
    errors = []
    for row in rows:
        errors.append(
            abs(
                prior
                + snow
                + row.rain_mm
                + row.irrigation_mm
                - row.runoff_mm
                - row.eta_mm
                - row.drainage_mm
                - row.profile_mm
                - row.snow_mm
            )
        )
        prior, snow = row.profile_mm, row.snow_mm
    return max(errors, default=0.0)


def paired(field, year, crop, calendar, *, weather_override=None, years=1, scenario=None):
    from field_shift_science.layered_water import balance as baseline
    from field_shift_science.layered_water.planting import CalendarParameters, Planting

    from .ruslan import balance as candidate
    from .ruslan.soil import CURVE_NUMBER_B, SoilHydraulics
    from .ruslan.weather import WeatherDay

    layers = [layer.model_dump() for layer in field.soil_layers]
    surface, depth, awc, _ = climate_balance._profile_layers(layers, 0.1)
    crop_parameters = CalendarParameters(
        crop_id=crop.id,
        sowing=date(year, 5, 1),
        harvest=date(year, 5, 1) + timedelta(days=sum(calendar.stage_lengths) - 1),
        stage_lengths=calendar.stage_lengths,
        kc_initial=crop.coefficients[0],
        kc_mid=crop.coefficients[1],
        kc_end=crop.coefficients[2],
        root_initial_m=0.15,
        root_max_m=min(crop.root_depth_m, depth),
        depletion_fraction=crop.depletion_fraction,
        height_m=1.0,
        residue_cover=0.3,
        cover_class="small_grain"
        if crop.id in {"wheat_spring", "barley", "oats", "millet"}
        else "row",
        dormancy=(),
        source=crop.coefficient_source_url + "; explicit common calendar " + calendar.id,
    )
    plantings = [
        Planting(
            replace(
                crop_parameters,
                sowing=crop_parameters.sowing.replace(year=year + offset),
                harvest=crop_parameters.harvest.replace(year=year + offset),
            )
        )
        for offset in range(years)
    ]
    weather = [
        WeatherDay(
            date=w.date,
            tmax=w.high_c,
            tmin=w.low_c,
            rain_mm=w.rain_mm,
            et0_mm=w.reference_et0_mm,
            source=field.weather_source_url,
        )
        for w in (field.seasons[year].days if weather_override is None else [])
    ]
    if weather_override is not None:
        weather = weather_override
    soil = SoilHydraulics(surface[2], surface[3], field.soil_source_url)
    first = candidate.Profile(soil, depth_m=depth, relative_wetness=0.5, layer_awc=awc)
    second = baseline.Profile(soil, depth_m=depth, relative_wetness=0.5, layer_awc=awc)
    opening = first.total()
    a = candidate.simulate(first, weather, plantings)
    b = baseline.simulate(
        second,
        weather,
        plantings,
        curve_numbers=CURVE_NUMBER_B,
        snow_temperature_c=candidate.SNOW_TMEAN_C,
        melt_mm_per_c_day=candidate.MELT_MM_PER_C_DAY,
        freeze_tmean_7d_c=candidate.FROZEN_TMEAN_7D_C,
        frozen_curve_number=candidate.FROZEN_CN,
        initial_residue_cover=candidate.DEFAULT_RESIDUE,
    )
    comparisons = {}
    for metric in ("profile_mm", "eta_mm", "etc_mm", "runoff_mm", "drainage_mm", "snow_mm"):
        delta = [getattr(x, metric) - getattr(y, metric) for x, y in zip(a, b, strict=True)]
        comparisons[metric] = {
            "agreement_mae_mm": fsum(abs(v) for v in delta) / len(delta),
            "agreement_rmse_mm": sqrt(fsum(v * v for v in delta) / len(delta)),
            "max_absolute_delta_mm": max(abs(v) for v in delta),
        }
    inputs = {
        "weather": [asdict(w) for w in weather],
        "calendar": asdict(crop_parameters),
        "repeated_annual_crop_years": years,
        "layers": layers,
        "opening_available_fraction": 0.5,
    }
    encoded = json.dumps(inputs, sort_keys=True, default=str, separators=(",", ":")).encode()
    return {
        "field_id": field.id,
        "region": "US-IA",
        "latitude": field.latitude,
        "longitude": field.longitude,
        "crop_id": crop.id,
        "year": year,
        "horizon_days": len(weather),
        "crop_years": years,
        "data_kind": "conditional_climate" if scenario else "historical_replay",
        "scenario": scenario,
        "calendar_id": calendar.id,
        "calendar_reference_region": calendar.reference_region,
        "input_sha256": sha(encoded),
        "comparisons": comparisons,
        "candidate_mass_error_mm": mass_error(a, opening),
        "baseline_mass_error_mm": mass_error(b, opening),
        "stress_disagreement_days": sum(x.stress != y.stress for x, y in zip(a, b, strict=True)),
        "candidate_total_demand_mm": fsum(r.etc_mm for r in a),
        "candidate_total_use_mm": fsum(r.eta_mm for r in a),
        "candidate_closing_profile_mm": a[-1].profile_mm,
        "observed_target_rows": 0,
    }


def baseline_identity():
    root = files("field_shift_science.layered_water")
    sources = {
        entry.name: sha(entry.read_bytes())
        for entry in sorted(root.iterdir(), key=lambda p: p.name)
        if entry.is_file() and entry.name.endswith(".py")
    }
    return {
        "distribution": "field-shift-science",
        "module": "layered_water",
        "source_files_sha256": sources,
        "sha256": sha(canonical(sources)),
    }


def run():
    resources = files("field_shift_rotation_model")
    expected = json.loads(
        resources.joinpath("examples/sutherland-2026-28/result-nasa-2027.json").read_text()
    )
    actual = calculate("sutherland_2027", {}, date.today())
    if actual != expected:
        raise AssertionError("Original Sutherland golden output changed")
    cases = []
    omissions = []
    for field_id in PROTOCOL["fields"]:
        field = load_fields()[field_id]
        for crop in crop_catalog():
            calendar = next((c for c in crop.calendars if sum(c.stage_lengths) <= 153), None)
            if calendar is None:
                omissions.append(
                    {
                        "field_id": field_id,
                        "crop_id": crop.id,
                        "reason": (
                            "No explicit calendar fitting complete153day source forcing; no "
                            "invented weather or durations"
                        ),
                    }
                )
                continue
            for year in (min(field.seasons), max(field.seasons)):
                cases.append(paired(field, year, crop, calendar))
    # Climate inputs are future conditional scenarios, never observed labels or forecasts.
    from .modules.rotations.climate_reference import load_thermal_reference

    engine = climate_balance._load_engine(climate_balance.engine_directory())
    profiles = {crop.id: crop for crop in crop_catalog()}
    for field_id, asset in (
        ("marsden_demo", "climate-weather"),
        ("sutherland_nw", "sutherland-weather"),
    ):
        field = load_fields()[field_id]
        payload = json.loads(
            gzip.decompress(resources.joinpath(f"examples/climate/{asset}.json.gz").read_bytes())
        )
        reference = load_thermal_reference(field.latitude, field.longitude)
        _, reference_metadata = climate_balance._historical_reference(
            reference, field.latitude, field.longitude
        )
        for years in (1, 2, 3):
            _, members = climate_balance._weather_series(
                payload,
                field.latitude,
                field.longitude,
                reference_metadata["elevation_m"],
                date(2040, 1, 1),
                date(2039 + years, 12, 31),
                engine,
            )
            for member, weather in members.items():
                for crop_id in ("maize_grain", "soybeans", "sunflower", "wheat_spring"):
                    crop = profiles[crop_id]
                    calendar = next(c for c in crop.calendars if sum(c.stage_lengths) <= 153)
                    cases.append(
                        paired(
                            field,
                            2040,
                            crop,
                            calendar,
                            weather_override=weather,
                            years=years,
                            scenario=member,
                        )
                    )
    regressions = [
        f"{c['field_id']}:{c['crop_id']}:{c['year']}"
        for c in cases
        if c["stress_disagreement_days"]
        or max(v["max_absolute_delta_mm"] for v in c["comparisons"].values())
        > PROTOCOL["agreement_tolerance_mm"]
        or max(c["candidate_mass_error_mm"], c["baseline_mass_error_mm"])
        > PROTOCOL["mass_closure_tolerance_mm"]
    ]
    return {
        "protocol": PROTOCOL,
        "protocol_sha256": sha(canonical(PROTOCOL)),
        "engine_version": ENGINE_VERSION,
        "baseline_identity": baseline_identity(),
        "outcome": "PASS" if not regressions else "FAIL",
        "golden_sutherland_exact": True,
        "golden_sutherland_sha256": sha(canonical(actual)),
        "cases": cases,
        "regressions": regressions,
        "omissions": omissions,
        "empirical_metrics": {
            "status": "blocked_missing_same_target_observations",
            "mae": None,
            "rmse": None,
            "r2": None,
            "bias": None,
            "coverage": None,
            "decision_regret": None,
            "reason": (
                "Bundled daily weather/soil references and national/trial yield "
                "summaries do not contain corresponding observed field crop ET, "
                "water shortfall or rotation profit labels. No synthetic or model "
                "output is used as accuracy truth."
            ),
        },
        "broader_scope": {
            "regions_not_measured": [
                "Europe",
                "South America",
                "equatorial",
                "arid",
                "paddy",
                "Antarctica",
                "high_altitude",
            ],
            "horizons_not_paired": ["full winter", "more than three crop years"],
            "reason": (
                "Current paired protocol uses complete actual same-input archive "
                "seasons and conditional NASA 1/2/3-year same-crop replays. "
                "Unsupported forcing gaps are not filled; multiyear weather is "
                "conditional, not an operational forecast or an observed target."
            ),
        },
        "promotion": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output must be a new path")
    report = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
    if report["outcome"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
