"""Train a fixed-cycle 1 m soil-water reserve correction and verify whole unseen sites."""

import argparse
import csv
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
from os import getenv
from pathlib import Path
from time import sleep

import numpy as np
from field_shift_science.crops.fao56 import crop_water_parameters
from field_shift_science.geo_ml.ifs import CURRENT_MODEL_CYCLE, HORIZON
from field_shift_science.simulation.root_zone_inputs import (
    RootZoneRun,
    RootZoneWeatherDay,
    parse_root_zone_runs,
    root_zone_run_url,
    root_zone_storage_mm,
)
from field_shift_science.simulation.root_zone_model import (
    CAPACITY_MM,
    DEPTH_M,
    FEATURE_NAMES,
    RootZoneCorrection,
    reference_reserve_mm,
    root_zone_features,
    root_zone_version,
)
from field_shift_science.simulation.water_balance import FAO56_BUCKET_VERSION, fao56_days
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from field_shift_ml.fetch_root_zone_observations import PROTOCOL_PATH, RootZoneSite
from field_shift_ml.train_moisture import download_features, evaluate_predictions, write_atomically


@dataclass(frozen=True, slots=True)
class Sample:
    site: RootZoneSite
    run: RootZoneRun
    day: RootZoneWeatherDay
    actual: float
    baseline: float
    raw_ifs: float


def load_targets(path: Path) -> tuple[dict, dict[str, dict[date, float]]]:
    manifest = json.loads(path.with_suffix(".manifest.json").read_bytes())
    if manifest["observations_sha256"] != sha256(path.read_bytes()).hexdigest():
        raise ValueError("Observation data and provenance do not match")
    if manifest["site_protocol_sha256"] != sha256(PROTOCOL_PATH.read_bytes()).hexdigest():
        raise ValueError("Observation data does not match the predeclared site protocol")
    targets = defaultdict(dict)
    with path.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            day = date.fromisoformat(row["date"])
            storage = float(row["storage_mm"])
            if day in targets[row["station_id"]] or not 0 < storage <= 800:
                raise ValueError("Invalid or duplicated root-zone observation")
            targets[row["station_id"]][day] = storage
    return manifest, targets


def archived_runs(
    sites: list[RootZoneSite], issue: date, cache: Path
) -> list[tuple[RootZoneSite, RootZoneRun]]:
    locations = [(site.latitude, site.longitude) for site in sites]
    key = sha256(root_zone_run_url(locations, issue, None).encode()).hexdigest()
    path = cache / f"{key}.json"
    if path.is_file():
        payload = json.loads(path.read_bytes())
    else:
        url = root_zone_run_url(locations, issue, getenv("OPEN_METEO_API_KEY"))
        for pause in (2, 5, 0):
            try:
                payload = download_features(url)
                break
            except json.JSONDecodeError as error:
                # The provider can time out a multi-coordinate run with an empty HTTP 200.
                if not pause:
                    if len(sites) == 1:
                        raise RuntimeError(f"Empty IFS archive response for {issue}") from error
                    split = len(sites) // 2
                    return archived_runs(sites[:split], issue, cache) + archived_runs(
                        sites[split:], issue, cache
                    )
                sleep(pause)
    response = parse_root_zone_runs(payload, locations, issue)
    if not path.exists():
        try:
            write_atomically(path, json.dumps(payload, separators=(",", ":")))
        except PermissionError:
            # Another process may publish and open the same completed cache entry on Windows.
            if not path.is_file():
                raise
    return list(zip(sites, response, strict=True))


def issue_samples(
    sites: list[RootZoneSite], issue: date, targets: dict, cache: Path, cycle: str
) -> list[Sample]:
    crop = crop_water_parameters("maize")
    assert crop is not None and crop.root_depth_m == DEPTH_M
    output = []
    for site, run in archived_runs(sites, issue, cache):
        if run.model_cycle != cycle:
            raise ValueError("IFS cycle disagrees with the site protocol")
        initial = reference_reserve_mm(root_zone_storage_mm(run.initial_layers, DEPTH_M))
        physical = fao56_days(
            run.days,
            initial_mm=initial,
            capacity_mm=CAPACITY_MM,
            threshold_mm=round((1 - crop.depletion_fraction) * CAPACITY_MM, 4),
            coefficient=crop.midseason_coefficient,
        )
        for day, balance in zip(run.days, physical, strict=True):
            actual = targets[site.station_id].get(day.date)
            if actual is not None and day.soil_temperature_c >= 2:
                output.append(
                    Sample(
                        site,
                        run,
                        day,
                        reference_reserve_mm(actual),
                        balance.reserve_mm,
                        reference_reserve_mm(root_zone_storage_mm(day.moisture_layers, DEPTH_M)),
                    )
                )
    print(f"Archived {cycle} issue {issue}: {len(output)} paired observations", flush=True)
    return output


def assemble(
    sites: list[RootZoneSite], window: dict, targets: dict, cache: Path, cycle: str
) -> list[Sample]:
    start, end = (date.fromisoformat(window[key]) for key in ("first_issue", "last_issue"))
    if start.weekday() != 0 or end.weekday() != 0:
        raise ValueError("The site protocol must pin Monday 00 UTC runs")
    dates = [start + timedelta(days=offset) for offset in range(0, (end - start).days + 1, 7)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        batches = pool.map(lambda day: issue_samples(sites, day, targets, cache, cycle), dates)
        return [sample for batch in batches for sample in batch]


def weights(samples: list[Sample]) -> np.ndarray:
    counts = Counter(sample.site.station_id for sample in samples)
    return np.array([len(samples) / len(counts) / counts[row.site.station_id] for row in samples])


def fit(samples: list[Sample]) -> tuple[float, list[float]]:
    predictors = np.array([root_zone_features(row.run, row.day, row.baseline) for row in samples])
    residuals = np.array([row.actual - row.baseline for row in samples])
    station_weights = weights(samples)
    scaler = StandardScaler().fit(predictors, sample_weight=station_weights)
    model = Ridge(alpha=40).fit(
        scaler.transform(predictors), residuals, sample_weight=station_weights
    )
    coefficients = 0.5 * model.coef_ / scaler.scale_
    return float(0.5 * model.intercept_ - coefficients @ scaler.mean_), coefficients.tolist()


def candidate(payload: dict) -> RootZoneCorrection:
    return RootZoneCorrection.model_validate({"version": root_zone_version(payload), **payload})


def calibrate(model: RootZoneCorrection, samples: list[Sample]) -> list[float]:
    widths = []
    for lead in range(1, HORIZON + 1):
        rows = [row for row in samples if row.day.lead_days == lead]
        errors = np.array(
            [abs(row.actual - model.predict(row.run, row.day, row.baseline)) for row in rows]
        )
        w = weights(rows)
        order = np.argsort(errors)
        index = np.searchsorted(np.cumsum(w[order]), 0.9 * w.sum())
        widths.append(float(max(0.01, errors[order[index]])))
    return widths


def score(model: RootZoneCorrection, samples: list[Sample]) -> dict:
    actual = np.array([row.actual for row in samples])
    baseline = np.array([row.baseline for row in samples])
    raw = np.array([row.raw_ifs for row in samples])
    learned = np.array([model.predict(row.run, row.day, row.baseline) for row in samples])
    width = np.array([model.interval_half_width_mm[row.day.lead_days - 1] for row in samples])
    w = weights(samples)
    return {
        "pairs": len(samples),
        "sites": len({row.site.station_id for row in samples}),
        "physical": evaluate_predictions(baseline, actual, w),
        "raw_ifs": evaluate_predictions(raw, actual, w),
        "learned": evaluate_predictions(learned, actual, w),
        "interval_coverage": float(np.average(np.abs(actual - learned) <= width, weights=w)),
    }


def evaluate(model: RootZoneCorrection, test: list[Sample], plan: dict) -> dict:
    counts = Counter(row.site.station_id for row in test)
    qualified = {
        site for site, count in counts.items() if count >= plan["minimum_holdout_pairs_per_farm"]
    }
    samples = [row for row in test if row.site.station_id in qualified]
    overall = score(model, samples) if samples else None
    leads = {}
    for lead in range(1, HORIZON + 1):
        rows = [row for row in samples if row.day.lead_days == lead]
        if rows:
            leads[str(lead)] = score(model, rows)
    farms = {
        site: score(model, [row for row in samples if row.site.station_id == site])
        for site in sorted(qualified)
    }
    checks = {
        "adequate_farms": len(qualified) >= 3,
        "improves_physical_mae_and_rmse": overall is not None
        and all(
            overall["learned"][metric] <= 0.95 * overall["physical"][metric]
            for metric in ("mae", "rmse")
        ),
        "improves_raw_ifs_rmse": overall is not None
        and overall["learned"]["rmse"] < overall["raw_ifs"]["rmse"],
        "interval_coverage": overall is not None and overall["interval_coverage"] >= 0.75,
        "farm_and_lead_stability": len(leads) == HORIZON
        and all(
            group["learned"]["rmse"] <= 1.10 * group["physical"]["rmse"]
            for group in (*leads.values(), *farms.values())
        ),
        "serving_cycle_validated": model.source_cycle == CURRENT_MODEL_CYCLE,
        "crop_depth_and_irrigation_verified": False,
    }
    return {
        "qualified_farms": overall,
        "leads": leads,
        "farms": farms,
        "predeclared_farm_pairs": {site: counts[site] for site in sorted(plan["evaluation_farms"])},
        "gates": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(PROTOCOL_PATH.read_bytes())
    cycle = plan["model_cycle"]
    manifest, targets = load_targets(args.observations)
    sites = [
        RootZoneSite(**{key: value for key, value in item.items() if key != "days"})
        for item in manifest["sites"]
        if item["days"]
    ]
    development = assemble(
        [site for site in sites if site.role != "test"],
        plan["fit_and_calibration"],
        targets,
        args.cache,
        cycle,
    )
    fitting = [row for row in development if row.site.role == "fit"]
    calibration = [row for row in development if row.site.role == "calibration"]
    intercept, coefficients = fit(fitting)
    coordinates = sorted({(row.site.latitude, row.site.longitude) for row in fitting})
    payload = {
        "schema_version": 1,
        "source_cycle": cycle,
        "reference_depth_m": 1,
        "feature_names": list(FEATURE_NAMES),
        "intercept": intercept,
        "weights": coefficients,
        "interval_half_width_mm": [20.0] * HORIZON,
        "fit_locations": [list(point) for point in coordinates],
        "validated_months": list(range(8, 11)),
        "promoted": False,
        "validation": {
            "observations_sha256": manifest["observations_sha256"],
            "site_protocol_sha256": manifest["site_protocol_sha256"],
            "baseline_version": FAO56_BUCKET_VERSION,
            "target": "0–1 m reference available reserve (mm) under fixed loam and maize inputs",
            "fit_pairs": len(fitting),
            "fit_sites": len(coordinates),
            "calibration_pairs": len(calibration),
            "calibration_sites": len({row.site.station_id for row in calibration}),
            "observation_sites": manifest["sites"],
            "excluded_sites": manifest["excluded"],
        },
    }
    payload["interval_half_width_mm"] = calibrate(candidate(payload), calibration)
    evaluation = assemble(
        [site for site in sites if site.role == "test"],
        plan["evaluation"],
        targets,
        args.cache,
        cycle,
    )
    report = evaluate(candidate(payload), evaluation, plan)
    payload["validation"].update(report)
    payload["promoted"] = all(report["gates"].values())
    model = candidate(payload)
    write_atomically(args.output, json.dumps(model.model_dump(mode="json"), indent=2) + "\n")
    print(json.dumps({"version": model.version, "promoted": model.promoted, **report}))


if __name__ == "__main__":
    main()
