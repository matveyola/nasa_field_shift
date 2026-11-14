"""Train on issued ECMWF forecasts and score on independent field probes."""

import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
from os import getenv
from pathlib import Path

import numpy as np
from field_shift_science.geo_ml.forecast_model import (
    FORECAST_FEATURES,
    ForecastCorrection,
    forecast_features,
    forecast_version,
)
from field_shift_science.geo_ml.ifs import (
    CURRENT_MODEL_CYCLE,
    HORIZON,
    IFSForecastDay,
    IFSForecastRun,
    ifs_run_url,
    parse_ifs_runs,
)
from field_shift_science.geo_ml.moisture_model import get_moisture_model, station_distance_km
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from field_shift_ml.train_moisture import (
    Station,
    download_features,
    evaluate_predictions,
    read_observations,
    write_atomically,
)


@dataclass(frozen=True, slots=True)
class ForecastSample:
    station: Station
    run: IFSForecastRun
    day: IFSForecastDay
    observed: float


def samples_for_run(
    stations: dict[Station, dict[date, float]], day: date, cache: Path
) -> list[ForecastSample]:
    sites = sorted(stations, key=lambda station: station.id)
    result = []
    for start in range(0, len(sites), 20):
        batch = sites[start : start + 20]
        locations = [(station.latitude, station.longitude) for station in batch]
        key = sha256(ifs_run_url(locations, day, None).encode()).hexdigest()
        cached = cache / f"{key}.json"
        if cached.is_file():
            payload = json.loads(cached.read_text(encoding="utf-8"))
        else:
            payload = download_features(ifs_run_url(locations, day, getenv("OPEN_METEO_API_KEY")))
        runs = parse_ifs_runs(payload, locations, day)
        if not cached.exists():
            write_atomically(cached, json.dumps(payload, separators=(",", ":")))
        for station, run in zip(batch, runs, strict=True):
            for valid in run.days:
                if valid.soil_temperature_c >= 2 and valid.date in stations[station]:
                    result.append(
                        ForecastSample(station, run, valid, stations[station][valid.date])
                    )
    print(f"Archived IFS {day}: {len(result)} paired samples", flush=True)
    return result


def load_period(stations, start: date, end: date, cache: Path) -> list[ForecastSample]:
    first = start + timedelta(days=(-start.weekday()) % 7)
    issues = [first + timedelta(days=offset) for offset in range(0, (end - first).days + 1, 7)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        return [
            sample
            for rows in pool.map(lambda day: samples_for_run(stations, day, cache), issues)
            for sample in rows
        ]


def station_weights(samples: list[ForecastSample]) -> np.ndarray:
    counts = Counter(sample.station for sample in samples)
    return np.array([len(samples) / len(counts) / counts[row.station] for row in samples])


def fit(samples: list[ForecastSample]) -> tuple[float, list[float]]:
    predictors = np.array([forecast_features(row.run, row.day) for row in samples])
    residual = np.array([row.observed - row.day.surface_moisture for row in samples])
    weights = station_weights(samples)
    scaler = StandardScaler().fit(predictors, sample_weight=weights)
    model = Ridge(alpha=40).fit(scaler.transform(predictors), residual, sample_weight=weights)
    coefficients = 0.5 * model.coef_ / scaler.scale_
    return float(0.5 * model.intercept_ - coefficients @ scaler.mean_), coefficients.tolist()


def model_from(payload: dict) -> ForecastCorrection:
    return ForecastCorrection.model_validate({"version": forecast_version(payload), **payload})


def benchmark(model: ForecastCorrection, samples: list[ForecastSample]) -> dict:
    actual = np.array([row.observed for row in samples])
    raw = np.array([row.day.surface_moisture for row in samples])
    corrected = np.array([model.predict(row.run, row.day) for row in samples])
    width = np.array([model.interval_half_width[row.day.lead_days - 1] for row in samples])
    weights = station_weights(samples)
    return {
        "days": len(samples),
        "sites": len({row.station for row in samples}),
        "ifs": evaluate_predictions(raw, actual, weights),
        "learned": evaluate_predictions(corrected, actual, weights),
        "interval_coverage": float(
            np.average(np.abs(actual - corrected) <= width, weights=weights)
        ),
    }


def calibrate(model: ForecastCorrection, samples: list[ForecastSample]) -> list[float]:
    widths = []
    for lead in range(1, HORIZON + 1):
        rows = [row for row in samples if row.day.lead_days == lead]
        errors = np.array([abs(row.observed - model.predict(row.run, row.day)) for row in rows])
        weights = station_weights(rows)
        order = np.argsort(errors)
        index = np.searchsorted(np.cumsum(weights[order]), 0.9 * weights.sum())
        widths.append(float(max(0.001, errors[order[index]])))
    return widths


def evaluate(model: ForecastCorrection, rows: list[ForecastSample]) -> dict:
    overall = benchmark(model, rows)
    leads = {
        str(lead): benchmark(model, [row for row in rows if row.day.lead_days == lead])
        for lead in range(1, HORIZON + 1)
    }
    farms = {
        site.id: benchmark(model, [row for row in rows if row.station == site])
        for site in sorted({row.station for row in rows}, key=lambda station: station.id)
    }
    checks = {
        "farm_coverage": len(farms) >= 3 and all(value["days"] >= 30 for value in farms.values()),
        "improvement": all(
            overall["learned"][metric] <= 0.95 * overall["ifs"][metric]
            for metric in ("mae", "rmse")
        ),
        "interval_coverage": overall["interval_coverage"] >= 0.75,
        "lead_and_farm_stability": all(
            value["learned"]["rmse"] <= 1.1 * value["ifs"]["rmse"]
            for value in (*leads.values(), *farms.values())
        ),
        "serving_cycle_validated": model.source_cycle == CURRENT_MODEL_CYCLE,
    }
    return {"independent_farms": overall, "leads": leads, "farms": farms, "gate": checks}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--new-farms", type=Path)
    args = parser.parse_args()
    manifest = get_moisture_model().validation["station_splits"]
    training = {site["station_id"] for site in manifest["train"]}
    calibration = {site["station_id"] for site in manifest["calibration"]}
    cohort = {
        site: days
        for site, days in read_observations(args.observations).items()
        if site.id in training | calibration
    }
    samples = load_period(cohort, date(2024, 4, 1), date(2024, 11, 15), args.cache)
    fitting = [row for row in samples if row.station.id in training]
    calibrating = [row for row in samples if row.station.id in calibration]
    intercept, weights = fit(fitting)
    fit_sites = sorted({(row.station.latitude, row.station.longitude) for row in fitting})
    payload = {
        "schema_version": 1,
        "source_cycle": "49R1",
        "feature_names": list(FORECAST_FEATURES),
        "intercept": intercept,
        "weights": weights,
        "interval_half_width": [0.1] * HORIZON,
        "fit_stations": [list(site) for site in fit_sites],
        "validated_months": list(range(4, 12)),
        "promoted": False,
        "validation": {
            "train_days": len(fitting),
            "train_sites": len({row.station for row in fitting}),
            "calibration_days": len(calibrating),
            "calibration_sites": len({row.station for row in calibrating}),
            "observation_sha256": sha256(args.observations.read_bytes()).hexdigest(),
        },
    }
    payload["interval_half_width"] = calibrate(model_from(payload), calibrating)
    if args.new_farms:
        test = read_observations(args.new_farms)
        if any(
            site.id in training | calibration
            or any(
                station_distance_km(site.latitude, site.longitude, other.latitude, other.longitude)
                < 40
                for other in cohort
            )
            for site in test
        ):
            raise ValueError("A test farm overlaps forecast fitting or calibration geography.")
        heldout = load_period(test, date(2025, 4, 1), date(2025, 11, 15), args.cache)
        summary = evaluate(model_from(payload), heldout)
        payload["validation"].update(summary)
        payload["validation"]["holdout_sha256"] = sha256(args.new_farms.read_bytes()).hexdigest()
        payload["promoted"] = all(summary["gate"].values())
    model = model_from(payload)
    write_atomically(args.output, json.dumps(model.model_dump(mode="json"), indent=2) + "\n")
    print(json.dumps({"version": model.version, "promoted": model.promoted, **model.validation}))


if __name__ == "__main__":
    main()
