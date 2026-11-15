"""Fit soil-conditioned surface moisture and score its frozen artifact on new farms."""

import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from statistics import median

import numpy as np
from field_shift_science.geo_ml.moisture_model import (
    ARTIFACT_NAME,
    SOIL_ATTRIBUTES,
    V2_ARTIFACT_SCHEMA,
    V2_FEATURE_NAMES,
    MoistureModel,
    MoistureModelV2,
    artifact_version,
    soil_moisture_features,
    station_distance_km,
)
from field_shift_science.geo_ml.ssurgo import SoilProfile
from sklearn import __version__ as sklearn_version
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler

from field_shift_ml.resources import evaluation_resource
from field_shift_ml.train_moisture import (
    Sample,
    Station,
    evaluate_predictions,
    join_station,
    join_stations,
    monthly_support,
    read_observations,
    separate_from,
    station_manifest,
    write_atomically,
)

V1_PATH = files("field_shift_science.geo_ml").joinpath(ARTIFACT_NAME)
V1_FARM_REPORT = evaluation_resource("iowa-fields-v1.json")
FIT_END = date(2024, 12, 31)
EVALUATION_YEAR = 2025
INTERVAL_LEVEL = 0.9
CANDIDATES = ("ridge", "ridge_half", "boosted", "boosted_half")
MIN_FARMS = 3
MIN_DAYS = 30


@dataclass(frozen=True)
class HoldoutSite:
    station: Station
    farm_id: str


def digest(path: Traversable) -> str:
    return sha256(path.read_bytes()).hexdigest()


def read_soils(path: Path) -> dict[str, SoilProfile]:
    return {
        key: SoilProfile.model_validate(profile)
        for key, profile in json.loads(path.read_text(encoding="utf-8-sig")).items()
        if profile is not None
    }


def read_reserved(path: Path, v1: MoistureModel) -> dict[str, HoldoutSite]:
    seen = [
        site for sites in v1.validation["station_splits"].values() for site in sites
    ] + json.loads(V1_FARM_REPORT.read_text(encoding="utf-8"))["sites"]
    sites = {}
    for row in json.loads(path.read_text(encoding="utf-8-sig"))["sites"]:
        station = Station(
            row["station_id"],
            row["latitude"],
            row["longitude"],
            row.get("network", "IFC"),
            row.get("location", row["station_id"]),
        )
        if station.id in sites or any(
            site["station_id"] == station.id
            or station_distance_km(
                station.latitude, station.longitude, site["latitude"], site["longitude"]
            )
            < 40
            for site in seen
        ):
            raise ValueError(f"Duplicate or v1-exposed holdout site {station.id}")
        sites[station.id] = HoldoutSite(station, row.get("farm_id", station.id))
    if len({site.farm_id for site in sites.values()}) < MIN_FARMS:
        raise ValueError("Register at least three separate new farms before fitting")
    return sites


def weights_for(keys: list[str]) -> np.ndarray:
    counts = Counter(keys)
    return np.asarray([len(keys) / (len(counts) * counts[key]) for key in keys])


def soil_medians(samples: list[Sample], soils: dict[str, SoilProfile]) -> tuple[float, ...]:
    stations = {sample.station.id for sample in samples}
    medians = []
    for attribute in SOIL_ATTRIBUTES:
        observed = [
            value
            for station in sorted(stations)
            if (value := getattr(soils[station], attribute)) is not None
        ]
        if not observed:
            raise ValueError(f"No fitting locations have the SSURGO property {attribute}")
        medians.append(float(median(observed)))
    return tuple(medians)


def feature_array(
    samples: list[Sample], soils: dict[str, SoilProfile], medians: tuple[float, ...]
) -> np.ndarray:
    return np.asarray(
        [
            soil_moisture_features(sample.day, soils[sample.station.id], medians)
            for sample in samples
        ]
    )


def targets(samples: list[Sample]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return (
        np.asarray([sample.value for sample in samples]),
        np.asarray([sample.day.era5_moisture for sample in samples]),
        weights_for([sample.station.id for sample in samples]),
    )


def available(
    samples: list[Sample], soils: dict[str, SoilProfile], *, include: set[str] | None = None
) -> list[Sample]:
    valid = set()
    for station in {sample.station for sample in samples}:
        if include is not None and station.id not in include:
            continue
        soil = soils.get(station.id)
        if soil is None or soil.sand is None or soil.clay is None:
            continue
        if (
            max(abs(station.latitude - soil.latitude), abs(station.longitude - soil.longitude))
            > 0.0001
        ):
            raise ValueError(f"Soil profile coordinates do not match {station.id}")
        valid.add(station)
    return [sample for sample in samples if sample.station in valid]


def fit_candidate(
    name: str, train: np.ndarray, actual: np.ndarray, raw: np.ndarray, weights: np.ndarray
) -> tuple[dict, np.ndarray]:
    residual = actual - raw
    strength = 0.5 if name.endswith("_half") else 1.0
    if name.startswith("ridge"):
        scaler = StandardScaler().fit(train, sample_weight=weights)
        normalized = scaler.transform(train)
        estimator = Ridge(alpha=300).fit(normalized, residual, sample_weight=weights)
        coefficients = estimator.coef_ / scaler.scale_ * strength
        fit = {
            "kind": "linear",
            "intercept": float(estimator.intercept_ * strength - coefficients @ scaler.mean_),
            "coefficients": coefficients.tolist(),
        }
        predicted = estimator.predict(normalized) * strength
    else:
        booster = GradientBoostingRegressor(
            n_estimators=80, max_depth=2, min_samples_leaf=100, learning_rate=0.045, random_state=97
        )
        booster.fit(train, residual, sample_weight=weights)
        fit = {
            "kind": "trees",
            "intercept": float(booster.init_.constant_.item() * strength),
            "learning_rate": booster.learning_rate * strength,
            "trees": [
                {
                    "nodes": [
                        [
                            -1 if left == -1 else int(feature),
                            float(threshold),
                            int(left),
                            int(right),
                            float(value),
                        ]
                        for feature, threshold, left, right, value in zip(
                            tree.feature,
                            tree.threshold,
                            tree.children_left,
                            tree.children_right,
                            tree.value[:, 0, 0],
                            strict=True,
                        )
                    ]
                }
                for tree in (item.tree_ for item in booster.estimators_[:, 0])
            ],
        }
        predicted = booster.predict(train) * strength
    return fit, np.clip(raw + predicted, 0, 1)


def build_artifact(payload: dict) -> dict:
    return {"version": artifact_version(payload), **payload}


def model_for(artifact: dict) -> MoistureModelV2:
    return MoistureModelV2.from_json(json.dumps(artifact, allow_nan=False).encode())


def corrections(fit: dict, features: np.ndarray) -> np.ndarray:
    if fit["kind"] == "linear":
        return fit["intercept"] + features @ np.asarray(fit["coefficients"])
    correction = np.full(len(features), fit["intercept"])
    tree_features = features.astype(np.float32)
    for tree in fit["trees"]:
        nodes = tree["nodes"]
        stack = [(0, np.arange(len(features)))]
        while stack:
            node, rows = stack.pop()
            index, threshold, left, right, value = nodes[node]
            if index == -1:
                correction[rows] += value * fit["learning_rate"]
            else:
                # scikit-learn's decision compares a cast float32 feature to a double threshold.
                goes_left = tree_features[rows, index].astype(np.float64) <= threshold
                stack.extend(((left, rows[goes_left]), (right, rows[~goes_left])))
    return correction


def choose_candidate(train: list[Sample], soils: dict[str, SoilProfile]) -> tuple[str, dict]:
    groups = [sample.station.block for sample in train]
    count = len(set(groups))
    if count < 3:
        raise ValueError("Need at least three buffered geographic fitting blocks")
    split = GroupKFold(n_splits=min(4, count))
    scores = {name: [] for name in CANDIDATES}
    for fitting, scoring in split.split(train, groups=groups):
        validation = [train[index] for index in scoring]
        held_out = {sample.station for sample in validation}
        fold_train = [
            train[index] for index in fitting if separate_from(train[index].station, held_out)
        ]
        if not fold_train:
            raise ValueError("Spatial buffering removed all fitting data in a fold")
        medians = soil_medians(fold_train, soils)
        training_features = feature_array(fold_train, soils, medians)
        validation_features = feature_array(validation, soils, medians)
        train_actual, train_raw, train_weights = targets(fold_train)
        actual, raw, weights = targets(validation)
        baseline = evaluate_predictions(raw, actual, weights)
        for name in CANDIDATES:
            fit, _ = fit_candidate(name, training_features, train_actual, train_raw, train_weights)
            predicted = np.clip(raw + corrections(fit, validation_features), 0, 1)
            score = evaluate_predictions(predicted, actual, weights)
            scores[name].append(float(np.mean([score[m] / baseline[m] for m in ("mae", "rmse")])))
    means = {key: float(np.mean(results)) for key, results in scores.items()}
    return min(means, key=means.__getitem__), means


def benchmark(
    model: MoistureModelV2,
    v1: MoistureModel,
    samples: list[Sample],
    soils: dict[str, SoilProfile],
    farm_ids: dict[str, str] | None = None,
) -> dict:
    if not samples:
        return {}
    groups = [farm_ids[sample.station.id] if farm_ids else sample.station.id for sample in samples]
    actual, raw, _ = targets(samples)
    weights = weights_for(groups)
    predictions = [model.predict(sample.day, soils[sample.station.id]) for sample in samples]
    values = np.asarray([prediction.value for prediction in predictions])
    prior = np.asarray([v1.predict(sample.day).value for sample in samples])
    covered = [
        prediction.lower <= value <= prediction.upper
        for prediction, value in zip(predictions, actual, strict=True)
    ]
    return {
        "days": len(samples),
        "site_count": len({sample.station for sample in samples}),
        "group_count": len(set(groups)),
        "model": evaluate_predictions(values, actual, weights),
        "era5_land": evaluate_predictions(raw, actual, weights),
        "v1": evaluate_predictions(prior, actual, weights),
        "interval_coverage": float(np.average(covered, weights=weights)),
    }


def evaluate_gate(summary: dict, farms: dict[str, dict], regional: dict) -> dict[str, bool]:
    enough = {farm: score for farm, score in farms.items() if score and score["days"] >= MIN_DAYS}
    metrics = ("mae", "rmse")
    return {
        "enough_farms": len(enough) >= MIN_FARMS and len(enough) == len(farms),
        "aggregate_gain": bool(summary)
        and all(
            summary["model"][metric] <= 0.95 * summary["era5_land"][metric] for metric in metrics
        ),
        "wins_most_farms": bool(enough)
        and sum(score["model"]["mae"] < score["era5_land"]["mae"] for score in enough.values()) * 3
        >= len(enough) * 2,
        "no_material_farm_degradation": bool(enough)
        and all(
            score["model"]["rmse"] <= 1.10 * score["era5_land"]["rmse"] for score in enough.values()
        ),
        "interval_coverage": bool(summary) and summary["interval_coverage"] >= 0.75,
        "regional_sanity": regional.get("site_count", 0) >= MIN_FARMS
        and all(regional["model"][metric] <= regional["era5_land"][metric] for metric in metrics),
    }


def fit(
    samples: list[Sample],
    soils: dict[str, SoilProfile],
    v1: MoistureModel,
    reserved: dict[str, HoldoutSite],
    inputs: dict[str, str],
) -> dict:
    reservations = {site.station for site in reserved.values()}
    source_splits = v1.validation["station_splits"]
    splits = {}
    for new_name, old_name in (
        ("train", "train"),
        ("calibration", "calibration"),
        ("regional", "holdout"),
    ):
        ids = {station["station_id"] for station in source_splits[old_name]}
        splits[new_name] = [
            sample
            for sample in available(samples, soils, include=ids)
            if (new_name == "regional" or separate_from(sample.station, reservations))
            and (
                sample.day.date.year == EVALUATION_YEAR
                if new_name == "regional"
                else sample.day.date <= FIT_END
            )
        ]
    training, calibration, regional = (
        splits[name] for name in ("train", "calibration", "regional")
    )
    if len({sample.station for sample in calibration}) < MIN_FARMS:
        raise ValueError("Need at least three geographically reserved calibration stations")
    name, scores = choose_candidate(training, soils)
    medians = soil_medians(training, soils)
    train_features = feature_array(training, soils, medians)
    estimator, expected = fit_candidate(name, train_features, *targets(training))
    locations = sorted({(sample.station.latitude, sample.station.longitude) for sample in training})
    payload = {
        "artifact_schema": V2_ARTIFACT_SCHEMA,
        "feature_names": list(V2_FEATURE_NAMES),
        "soil_medians": list(medians),
        "promoted": False,
        "fit": estimator,
        "coverage": {
            "bounds": v1.coverage["bounds"],
            "station_locations": [list(location) for location in locations],
            "max_station_distance_km": v1.coverage["max_station_distance_km"],
            "validated_months": [
                month for month, score in monthly_support(regional).items() if score["supported"]
            ],
        },
        "interval_level": INTERVAL_LEVEL,
        "interval_half_width": 0.1,
        "validation": {},
    }
    model = model_for(build_artifact(payload))
    portable = [model.predict(sample.day, soils[sample.station.id]).value for sample in training]
    if not np.allclose(portable, expected, rtol=0, atol=1e-9):
        raise ValueError("Portable soil model differs from its training estimator")
    if not np.allclose(
        np.clip(train_features[:, 0] + corrections(estimator, train_features), 0, 1),
        expected,
        rtol=0,
        atol=1e-9,
    ):
        raise ValueError("Cross-validation predictor differs from the fitted estimator")
    actual, _, weights = targets(calibration)
    predicted = np.asarray(
        [model.predict(sample.day, soils[sample.station.id]).value for sample in calibration]
    )
    errors = np.abs(predicted - actual)
    order = np.argsort(errors)
    quantile = np.searchsorted(np.cumsum(weights[order]), INTERVAL_LEVEL * weights.sum())
    payload["interval_half_width"] = float(max(0.001, errors[order[quantile]]))
    model = model_for(build_artifact(payload))
    payload["validation"] = {
        "candidate": name,
        "blocked_cv_baseline_normalized_mae_rmse": scores,
        "paired_station_days": {key: len(value) for key, value in splits.items()},
        "station_splits": {key: station_manifest(value) for key, value in splits.items()},
        "regional": benchmark(model, v1, regional, soils),
        "fit_end": FIT_END.isoformat(),
        "evaluation_year": EVALUATION_YEAR,
        "sklearn_version": sklearn_version,
        "inputs": inputs,
        "independent_farms": None,
    }
    return build_artifact(payload)


def evaluate(
    frozen: dict,
    samples: list[Sample],
    soils: dict[str, SoilProfile],
    v1: MoistureModel,
    reserved: dict[str, HoldoutSite],
) -> dict:
    model = model_for(frozen)
    for station in {sample.station for sample in samples}:
        site = reserved.get(station.id)
        if (
            site is None
            or max(
                abs(station.latitude - site.station.latitude),
                abs(station.longitude - site.station.longitude),
            )
            > 0.0001
        ):
            raise ValueError(f"Unregistered holdout station {station.id}")
    matched = [
        sample for sample in available(samples, soils) if sample.day.date.year == EVALUATION_YEAR
    ]
    farm_ids = {key: site.farm_id for key, site in reserved.items()}
    farm_samples = {farm: [] for farm in sorted(set(farm_ids.values()))}
    for sample in matched:
        farm_samples[farm_ids[sample.station.id]].append(sample)
    farms = {farm: benchmark(model, v1, rows, soils) for farm, rows in farm_samples.items()}
    qualified = [
        sample for rows in farm_samples.values() if len(rows) >= MIN_DAYS for sample in rows
    ]
    summary = benchmark(model, v1, qualified, soils, farm_ids)
    operational = [
        sample
        for sample in qualified
        if model.supports_location(sample.station.latitude, sample.station.longitude)
        and model.supports_date(sample.day.date)
    ]
    return {
        "summary": summary,
        "operational_scope_diagnostic": benchmark(model, v1, operational, soils, farm_ids),
        "interval_coverage": summary.get("interval_coverage"),
        "farms": farms,
        "matched_stations": station_manifest(matched),
        "insufficient_pair_counts": {
            farm: len(rows) for farm, rows in farm_samples.items() if len(rows) < MIN_DAYS
        },
        "promotion_checks": evaluate_gate(summary, farms, frozen["validation"]["regional"]),
    }


def join_reserved(observations: dict[Station, dict[date, float]], cache: Path) -> list[Sample]:
    # Keep the real matched counts for registered farms that miss the release minimum.
    with ThreadPoolExecutor(max_workers=3) as executor:
        pending = [
            executor.submit(join_station, station, dates, cache)
            for station, dates in sorted(observations.items(), key=lambda item: item[0].id)
        ]
        return [sample for future in pending for sample in future.result()]


def save(path: Path, payload: dict) -> None:
    write_atomically(path, json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("fit", "evaluate"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--soil", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frozen", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    v1 = MoistureModel.from_json(V1_PATH.read_bytes())
    reserved = read_reserved(args.manifest, v1)
    soils = read_soils(args.soil)
    if args.phase == "fit":
        artifact = fit(
            join_stations(read_observations(args.observations), args.cache),
            soils,
            v1,
            reserved,
            {
                "holdout_manifest_sha256": digest(args.manifest),
                "fitting_observations_sha256": digest(args.observations),
                "fitting_soils_sha256": digest(args.soil),
                "v1_artifact_sha256": digest(V1_PATH),
            },
        )
    else:
        if not args.frozen:
            parser.error("evaluate requires --frozen")
        frozen = json.loads(args.frozen.read_text(encoding="utf-8-sig"))
        if frozen["validation"]["inputs"]["holdout_manifest_sha256"] != digest(args.manifest):
            raise ValueError("Holdout manifest has changed since fitting")
        if frozen["validation"]["independent_farms"] is not None:
            raise ValueError("Supply the frozen pre-evaluation artifact")
        report = evaluate(
            frozen,
            join_reserved(read_observations(args.observations), args.cache),
            soils,
            v1,
            reserved,
        )
        report.update(
            {
                "frozen_version": frozen["version"],
                "frozen_artifact_sha256": digest(args.frozen),
                "observations_sha256": digest(args.observations),
                "soils_sha256": digest(args.soil),
            }
        )
        payload = {key: value for key, value in frozen.items() if key != "version"}
        payload["validation"]["independent_farms"] = report
        payload["promoted"] = all(report["promotion_checks"].values())
        artifact = build_artifact(payload)
    model_for(artifact)
    save(args.output, artifact)
    if args.report is not None and args.phase == "evaluate":
        save(args.report, {"model_version": artifact["version"], **report})
    print(
        json.dumps(
            {
                "version": artifact["version"],
                "promoted": artifact["promoted"],
                "candidate": artifact["validation"]["candidate"],
                "regional": artifact["validation"]["regional"],
                "farm_comparison": artifact["validation"]["independent_farms"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
