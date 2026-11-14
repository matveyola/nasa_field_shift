"""Fit and benchmark a 5 cm soil moisture correction using ground observations."""

import argparse
import csv
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from hashlib import sha256
from math import floor, isfinite
from os import getenv
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
from sklearn import __version__ as sklearn_version
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler

from field_shift.modules.geo_ml.era5_land import MoistureSourceError
from field_shift.modules.geo_ml.moisture_features import (
    FEATURE_NAMES,
    MoistureFeatures,
    moisture_feature_url,
    parse_moisture_features,
)
from field_shift.modules.geo_ml.moisture_model import (
    ARTIFACT_SCHEMA,
    MoistureModel,
    artifact_version,
    station_distance_km,
)

_FIT_END = date(2024, 12, 31)
_HOLDOUT_START = date(2025, 1, 1)
_HOLDOUT_END = date(2025, 12, 31)
_BOUNDS = (36.0, -102.0, 50.0, -80.0)
_BUFFER_KM = 40
_SERVING_RADIUS_KM = 180.0
_NOMINAL_COVERAGE = 0.9
_MINIMUM_RELATIVE_GAIN = 0.03
_MINIMUM_HOLDOUT_COVERAGE = 0.70
_MINIMUM_HOLDOUT_STATIONS = 3
_MINIMUM_STATION_DAYS = 30
_MAX_FEATURE_BYTES = 512 * 1024
_CANDIDATES = ("ridge", "boosted", "ridge_half")
_EXTERNAL_STATIONS = (
    "SCAN:2031:IA:SCAN",
    "SCAN:2224:MO:SCAN",
    "SCAN:2001:NE:SCAN",
    "SCAN:2014:OH:SCAN",
)


@dataclass(frozen=True, slots=True)
class Station:
    id: str
    latitude: float
    longitude: float
    network: str
    location: str

    @property
    def block(self) -> str:
        return f"{floor(self.latitude / 2)}:{floor(self.longitude / 2)}"


@dataclass(frozen=True, slots=True)
class Sample:
    station: Station
    day: MoistureFeatures
    value: float


def read_observations(path: Path) -> dict[Station, dict[date, float]]:
    grouped: dict[Station, dict[date, float]] = {}
    identities: dict[str, Station] = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            station = Station(
                row["station_id"],
                float(row["latitude"]),
                float(row["longitude"]),
                row.get("network", ""),
                row.get("location", ""),
            )
            if identities.setdefault(station.id, station) != station:
                raise ValueError(f"Inconsistent metadata for {station.id}")
            day, value = date.fromisoformat(row["date"]), float(row["moisture"])
            if not (
                0 <= value <= 1
                and _BOUNDS[0] <= station.latitude <= _BOUNDS[2]
                and _BOUNDS[1] <= station.longitude <= _BOUNDS[3]
            ):
                continue
            existing = grouped.setdefault(station, {}).setdefault(day, value)
            if existing != value:
                raise ValueError(f"Conflicting observations for {station.id} on {day}")
    return grouped


def write_atomically(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as stream:
        stream.write(payload)
        temporary = Path(stream.name)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def retry_after_seconds(header: str | None, fallback: float, *, maximum: float = 120.0) -> float:
    if not header:
        return fallback
    try:
        seconds = float(header)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError):
            return fallback
    return max(1.0, min(seconds, maximum)) if isfinite(seconds) else fallback


def download_features(url: str) -> dict:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "FieldShift/0.1"})
    for pause in (2, 5, 12, 0):
        try:
            with urlopen(request, timeout=25) as response:
                encoded = response.read(_MAX_FEATURE_BYTES + 1)
            if len(encoded) > _MAX_FEATURE_BYTES:
                raise ValueError("Archive data exceeds the training file size limit")
            return json.loads(encoded)
        except HTTPError as error:
            status = error.code
            retry_after = error.headers.get("Retry-After")
            error.close()
            if not pause or status not in (408, 429, 500, 502, 503, 504):
                raise MoistureSourceError(f"Feature archive returned HTTP {status}") from None
            if status == 429 and retry_after_seconds(retry_after, 65.0, maximum=float("inf")) > 120:
                raise MoistureSourceError(
                    "Feature archive rate limit exceeds the offline retry window; use the cache"
                ) from None
            pause = retry_after_seconds(retry_after, 65.0 if status == 429 else pause)
        except OSError:
            if not pause:
                raise MoistureSourceError("Feature archive connection failed") from None
        time.sleep(pause)
    raise AssertionError("The final archive fetch returns or raises")


def join_station(station: Station, observations: dict[date, float], cache: Path) -> list[Sample]:
    start, end = date(min(observations).year, 1, 1), date(max(observations).year, 12, 31)
    endpoint = moisture_feature_url(station.latitude, station.longitude, start, end, None)
    key = sha256(endpoint.encode("utf-8")).hexdigest()
    path = cache / f"{key}.json"
    if path.exists():
        response = json.loads(path.read_text(encoding="utf-8"))
    else:
        url = moisture_feature_url(
            station.latitude, station.longitude, start, end, getenv("OPEN_METEO_API_KEY")
        )
        response = download_features(url)
    frame = parse_moisture_features(response, station.latitude, station.longitude, start, end)
    if not frame.days:
        raise ValueError(f"No complete ERA5-Land predictors for {station.id}")
    if not path.exists():
        write_atomically(path, json.dumps(response, separators=(",", ":")))
    return [
        Sample(station, day, observations[day.date])
        for day in frame.days
        if day.date in observations and not day.is_frozen
    ]


def join_stations(observations: dict[Station, dict[date, float]], cache: Path) -> list[Sample]:
    by_station = {}
    failures = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        pending = {
            executor.submit(join_station, station, days, cache): station.id
            for station, days in observations.items()
            if len(days) >= _MINIMUM_STATION_DAYS
        }
        for future in as_completed(pending):
            station_id = pending[future]
            try:
                by_station[station_id] = future.result()
                print(f"Joined {station_id}: {len(by_station[station_id])} thawed days", flush=True)
            except (MoistureSourceError, ValueError) as error:
                failures.append(f"{station_id}: {error}")
    if failures:
        raise RuntimeError("Archive feature acquisition failed for: " + "; ".join(failures))
    return [
        row
        for station_id in sorted(by_station)
        for row in by_station[station_id]
        if len(by_station[station_id]) >= _MINIMUM_STATION_DAYS
    ]


def separate_from(station: Station, other_stations: set[Station]) -> bool:
    return all(
        station.block != other.block
        and station_distance_km(
            station.latitude, station.longitude, other.latitude, other.longitude
        )
        >= _BUFFER_KM
        for other in other_stations
    )


def split_samples(samples: list[Sample], external_ids: set[str]) -> dict[str, list[Sample]]:
    early_counts = Counter(sample.station for sample in samples if sample.day.date <= _FIT_END)
    late_counts = Counter(
        sample.station for sample in samples if _HOLDOUT_START <= sample.day.date <= _HOLDOUT_END
    )
    external = {
        station
        for station in early_counts.keys() | late_counts.keys()
        if station.id in external_ids
        and early_counts[station] + late_counts[station] >= _MINIMUM_STATION_DAYS
    }
    if external_ids - {station.id for station in external}:
        raise ValueError("Requested independent sites lack usable observations")
    external_with_late_data = {
        station for station in external if late_counts[station] >= _MINIMUM_STATION_DAYS
    }
    available = {
        station
        for station, count in early_counts.items()
        if count >= _MINIMUM_STATION_DAYS and separate_from(station, external)
    }
    eligible_holdout_blocks = {
        station.block for station in available if late_counts[station] >= _MINIMUM_STATION_DAYS
    }
    blocks = sorted(
        eligible_holdout_blocks,
        key=lambda block: sha256(f"soil-moisture-v1:{block}".encode()).hexdigest(),
    )
    if len(blocks) < 8:
        raise ValueError("Need eight distinct eligible spatial blocks for train/calibration/test")
    test_block_count = max(_MINIMUM_HOLDOUT_STATIONS, round(0.2 * len(blocks)))
    test_blocks = set(blocks[:test_block_count])
    test_locations = {station for station in available if station.block in test_blocks}
    test = {station for station in test_locations if late_counts[station] >= _MINIMUM_STATION_DAYS}
    remaining = {station for station in available if separate_from(station, test_locations)}
    calibration_block_count = max(3, round(0.2 * len(blocks)))
    calibration_blocks = [
        block
        for block in blocks[test_block_count:]
        if any(
            station.block == block and early_counts[station] >= _MINIMUM_STATION_DAYS
            for station in remaining
        )
    ][:calibration_block_count]
    calibration = {station for station in remaining if station.block in calibration_blocks}
    train = {station for station in remaining if separate_from(station, calibration)}
    if len({station.block for station in train}) < 3 or len(calibration) < 3:
        raise ValueError("Too few remaining spatial groups after buffering independent sites")
    return {
        "train": [s for s in samples if s.station in train and s.day.date <= _FIT_END],
        "calibration": [s for s in samples if s.station in calibration and s.day.date <= _FIT_END],
        "holdout": [
            s for s in samples if s.station in test and _HOLDOUT_START <= s.day.date <= _HOLDOUT_END
        ],
        "independent_farms": [
            s
            for s in samples
            if s.station in external_with_late_data and _HOLDOUT_START <= s.day.date <= _HOLDOUT_END
        ],
        "independent_farms_spatial_only": [
            s
            for s in samples
            if s.station in external - external_with_late_data and s.day.date <= _FIT_END
        ],
    }


def to_arrays(samples: list[Sample]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    counts = Counter(sample.station.id for sample in samples)
    station_weight = len(samples) / len(counts)
    return (
        np.array([sample.day.values for sample in samples]),
        np.array([sample.value for sample in samples]),
        np.array([station_weight / counts[sample.station.id] for sample in samples]),
    )


def fit_candidate(name: str, samples: list[Sample]) -> tuple[dict, np.ndarray]:
    features, values, weights = to_arrays(samples)
    residual = values - features[:, 0]
    if name == "boosted":
        estimator = GradientBoostingRegressor(
            n_estimators=110, max_depth=2, min_samples_leaf=45, learning_rate=0.04, random_state=89
        )
        estimator.fit(features, residual, sample_weight=weights)
        fit = {
            "kind": "trees",
            "intercept": float(estimator.init_.constant_[0, 0]),
            "learning_rate": float(estimator.learning_rate),
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
                for tree in (element.tree_ for element in estimator.estimators_[:, 0])
            ],
        }
        predicted = estimator.predict(features)
    else:
        scaler = StandardScaler().fit(features, sample_weight=weights)
        normalized = scaler.transform(features)
        estimator = Ridge(alpha=40).fit(normalized, residual, sample_weight=weights)
        strength = 0.5 if name == "ridge_half" else 1.0
        coefficients = estimator.coef_ / scaler.scale_ * strength
        fit = {
            "kind": "linear",
            "intercept": float(estimator.intercept_ * strength - coefficients @ scaler.mean_),
            "coefficients": coefficients.tolist(),
        }
        predicted = estimator.predict(normalized) * strength
    return fit, np.clip(features[:, 0] + predicted, 0, 1)


def build_artifact(
    fit: dict, train: list[Sample], *, validated_months: list[int] | None = None, **kwargs
) -> dict:
    payload = {
        "artifact_schema": ARTIFACT_SCHEMA,
        "feature_names": list(FEATURE_NAMES),
        "promoted": False,
        "fit": fit,
        "coverage": {
            "bounds": list(_BOUNDS),
            "station_locations": [
                list(pair)
                for pair in sorted(
                    {(sample.station.latitude, sample.station.longitude) for sample in train}
                )
            ],
            "max_station_distance_km": _SERVING_RADIUS_KM,
            "validated_months": validated_months or [],
        },
        "interval_level": _NOMINAL_COVERAGE,
        "interval_half_width": 0.1,
        "validation": {},
        **kwargs,
    }
    return {"version": artifact_version(payload), **payload}


def model_for(artifact: dict) -> MoistureModel:
    return MoistureModel.from_json(json.dumps(artifact, allow_nan=False).encode())


def evaluate_predictions(
    predicted: np.ndarray, actual: np.ndarray, weights: np.ndarray
) -> dict[str, float]:
    error = predicted - actual
    return {
        "mae": float(np.average(np.abs(error), weights=weights)),
        "rmse": float(np.sqrt(np.average(error**2, weights=weights))),
        "bias": float(np.average(error, weights=weights)),
    }


def select_candidate(train: list[Sample]) -> tuple[str, dict]:
    groups = [sample.station.block for sample in train]
    splitter = GroupKFold(n_splits=min(4, len(set(groups))))
    scores = {name: [] for name in _CANDIDATES}
    for train_idx, validation_idx in splitter.split(train, groups=groups):
        validation = [train[index] for index in validation_idx]
        validation_stations = {sample.station for sample in validation}
        fit_stations = {
            train[index].station
            for index in train_idx
            if separate_from(train[index].station, validation_stations)
        }
        fitting = [train[index] for index in train_idx if train[index].station in fit_stations]
        if not fitting:
            raise ValueError("Spatial buffering left a cross-validation fold without training data")
        _, values, weights = to_arrays(validation)
        for name in _CANDIDATES:
            fit, _ = fit_candidate(name, fitting)
            model = model_for(build_artifact(fit, fitting))
            predicted = np.array([model.predict(sample.day).value for sample in validation])
            scores[name].append(evaluate_predictions(predicted, values, weights)["rmse"])
    means = {name: float(np.mean(folds)) for name, folds in scores.items()}
    return min(means, key=means.__getitem__), means


def quantify_interval(model: MoistureModel, calibration: list[Sample]) -> float:
    _, actual, weights = to_arrays(calibration)
    predicted = np.array([model.predict(sample.day).value for sample in calibration])
    errors = np.abs(predicted - actual)
    order = np.argsort(errors)
    threshold = _NOMINAL_COVERAGE * np.sum(weights)
    index = np.searchsorted(np.cumsum(weights[order]), threshold)
    return float(max(errors[order[index]], 0.001))


def benchmark(
    model: MoistureModel, samples: list[Sample], climatology: float | None = None
) -> dict:
    features, actual, weights = to_arrays(samples)
    predictions = [model.predict(sample.day) for sample in samples]
    values = np.array([prediction.value for prediction in predictions])
    hits = np.array(
        [
            prediction.lower <= value <= prediction.upper
            for prediction, value in zip(predictions, actual, strict=True)
        ]
    )
    return {
        "days": len(samples),
        "station_count": len({sample.station for sample in samples}),
        "model": evaluate_predictions(values, actual, weights),
        "era5_land": evaluate_predictions(features[:, 0], actual, weights),
        **(
            {
                "training_climatology": evaluate_predictions(
                    np.full_like(actual, climatology), actual, weights
                ),
            }
            if climatology is not None
            else {}
        ),
        "interval_coverage": float(np.average(hits, weights=weights)),
    }


def station_manifest(samples: list[Sample]) -> list[dict]:
    days: dict[Station, list[date]] = {}
    for sample in samples:
        days.setdefault(sample.station, []).append(sample.day.date)
    return [
        {
            "station_id": station.id,
            "location": station.location,
            "network": station.network,
            "latitude": station.latitude,
            "longitude": station.longitude,
            "block": station.block,
            "days": len(days[station]),
            "from": min(days[station]).isoformat(),
            "to": max(days[station]).isoformat(),
        }
        for station in sorted(days, key=lambda station: station.id)
    ]


def monthly_support(samples: list[Sample]) -> dict[int, dict]:
    supported = {}
    for month in range(1, 13):
        rows = [sample for sample in samples if sample.day.date.month == month]
        count = len({sample.station for sample in rows})
        supported[month] = {
            "days": len(rows),
            "station_count": count,
            "supported": len(rows) >= _MINIMUM_STATION_DAYS and count >= _MINIMUM_HOLDOUT_STATIONS,
        }
    return supported


def promotion_checks(holdout: dict, independent: dict | None) -> dict[str, bool]:
    return {
        "enough_unseen_stations": holdout["station_count"] >= _MINIMUM_HOLDOUT_STATIONS,
        **{
            f"{metric}_improves_over_era5_land": (
                holdout["model"][metric]
                < (1 - _MINIMUM_RELATIVE_GAIN) * holdout["era5_land"][metric]
            )
            for metric in ("mae", "rmse")
        },
        "interval_coverage": holdout["interval_coverage"] >= _MINIMUM_HOLDOUT_COVERAGE,
        "enough_independent_farms_in_evaluation_period": (
            independent is not None and independent["station_count"] >= _MINIMUM_HOLDOUT_STATIONS
        ),
        "independent_farms_do_not_both_degrade_materially": independent is not None
        and any(
            independent["model"][metric] <= 1.10 * independent["era5_land"][metric]
            for metric in ("mae", "rmse")
        ),
    }


def train(samples: list[Sample], external_ids: set[str], observation_sha256: str) -> dict:
    split = split_samples(samples, external_ids)
    fitting = split["train"]
    selected, scores = select_candidate(fitting)
    fit, original = fit_candidate(selected, fitting)
    model = model_for(build_artifact(fit, fitting))
    portable = [model.predict(sample.day).value for sample in fitting]
    if not np.allclose(portable, original, rtol=0, atol=1e-9):
        raise ValueError("Portable model does not reproduce the training estimator")
    width = quantify_interval(model, split["calibration"])
    model = model_for(build_artifact(fit, fitting, interval_half_width=width))
    _, actual, weights = to_arrays(fitting)
    climatology = float(np.average(actual, weights=weights))
    holdout = benchmark(model, split["holdout"], climatology)
    independent = (
        benchmark(model, split["independent_farms"], climatology)
        if split["independent_farms"]
        else None
    )
    spatial_only = (
        benchmark(model, split["independent_farms_spatial_only"], climatology)
        if split["independent_farms_spatial_only"]
        else None
    )
    checks = promotion_checks(holdout, independent)
    support = monthly_support(split["holdout"] + split["independent_farms"])
    joined_hash = sha256()
    for sample in samples:
        line = (sample.station.id, sample.day.date.isoformat(), sample.value, *sample.day.values)
        joined_hash.update((json.dumps(line, separators=(",", ":")) + "\n").encode())
    return build_artifact(
        fit,
        fitting,
        promoted=all(checks.values()),
        interval_half_width=width,
        validated_months=[month for month, summary in support.items() if summary["supported"]],
        validation={
            "holdout": holdout,
            "independent_farms": independent,
            "independent_farms_spatial_only": spatial_only,
            "station_splits": {key: station_manifest(value) for key, value in split.items()},
            "monthly_heldout_support": {str(month): summary for month, summary in support.items()},
            "fit_end": _FIT_END.isoformat(),
            "evaluation_start": _HOLDOUT_START.isoformat(),
            "evaluation_end": _HOLDOUT_END.isoformat(),
            "spatial_buffer_km": _BUFFER_KM,
            "promotion_checks": checks,
            "selected_candidate": selected,
            "train_group_cv_station_balanced_rmse": scores,
            "sklearn_version": sklearn_version,
            "observation_csv_sha256": observation_sha256,
            "joined_samples_sha256": joined_hash.hexdigest(),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--external-station", action="append")
    args = parser.parse_args()
    observations = read_observations(args.observations)
    samples = join_stations(observations, args.cache)
    artifact = train(
        samples,
        set(args.external_station or _EXTERNAL_STATIONS),
        sha256(args.observations.read_bytes()).hexdigest(),
    )
    model_for(artifact)
    if not all(isfinite(value) for value in artifact["validation"]["holdout"]["model"].values()):
        raise ValueError("Non-finite evaluation metric")
    write_atomically(args.output, json.dumps(artifact, indent=2, sort_keys=True, allow_nan=False))
    print(
        json.dumps(
            {
                "version": artifact["version"],
                "promoted": artifact["promoted"],
                "holdout": artifact["validation"]["holdout"],
                "independent_farms": artifact["validation"]["independent_farms"],
                "promotion_checks": artifact["validation"]["promotion_checks"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
