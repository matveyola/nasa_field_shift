"""Evaluate expanded post-processing of issued five-day surface moisture forecasts."""

import argparse
import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from hashlib import sha256
from importlib.resources.abc import Traversable
from math import exp, log1p
from pathlib import Path

import numpy as np
from field_shift_science.geo_ml.forecast_model import FORECAST_FEATURES, forecast_features
from field_shift_science.geo_ml.ifs import CURRENT_MODEL_CYCLE, HORIZON, ifs_run_url, parse_ifs_runs
from field_shift_science.geo_ml.moisture_model import get_moisture_model, station_distance_km
from field_shift_science.geo_ml.ssurgo import SoilProfile
from field_shift_science.simulation.root_zone_inputs import root_zone_run_url
from sklearn import __version__ as sklearn_version
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, HuberRegressor, Ridge
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import PolynomialFeatures, StandardScaler
from threadpoolctl import threadpool_limits

from field_shift_ml.fetch_surface_forecast_observations import PROTOCOL as POSTCYCLE_PROTOCOL
from field_shift_ml.train_moisture import (
    Station,
    evaluate_predictions,
    read_observations,
    separate_from,
    write_atomically,
)
from field_shift_ml.train_moisture_forecast import ForecastSample, load_period
from field_shift_ml.train_moisture_v2 import read_soils

BUFFER_KM = 40
MIN_TEST_PAIRS = 30
SERVING_RADIUS_KM = 180
NOMINAL_INTERVAL = 0.90
RANK_TOLERANCE = 0.01
SEED = 1729
MISSING = float("nan")
DEVELOPMENT_WINDOW = (date(2024, 4, 1), date(2024, 11, 15))
KNOWN_FARM_WINDOW = (date(2025, 4, 1), date(2025, 11, 15))

FEATURE_NAMES = {
    "v1": FORECAST_FEATURES,
    "layers": (
        "surface_minus_subsurface",
        "surface_change_since_issue",
        "surface_day_change",
        "subsurface_change_since_first_lead",
        "prior_lead_surface_mean",
        "prior_lead_surface_range",
    ),
    "fluxes": (
        "log_daily_precipitation",
        "daily_et0",
        "recent_precipitation",
        "recent_et0",
        "cumulative_water_balance",
        "number_of_wet_days",
        "days_since_precipitation_in_run",
        "soil_minus_air_temperature",
        "precipitation_times_initial_dryness",
    ),
    "soil": (
        "sand_pct",
        "silt_pct",
        "clay_pct",
        "organic_matter_pct",
        "bulk_density",
        "field_capacity",
        "wilting_point",
        "available_water_capacity",
        "drainage_class",
        "surface_minus_field_capacity",
        "initial_relative_available_water",
        "precipitation_times_sand_fraction",
    ),
}
FEATURE_SETS = {
    "v1": ("v1",),
    "layers": ("v1", "layers"),
    "fluxes": ("v1", "fluxes"),
    "soil": ("v1", "soil"),
    "dynamic": ("v1", "layers", "fluxes"),
    "all_without_layers": ("v1", "fluxes", "soil"),
    "all_without_fluxes": ("v1", "layers", "soil"),
    "all": ("v1", "layers", "fluxes", "soil"),
}
_DRAINAGE = {
    value: order
    for order, value in enumerate(
        (
            "very poorly drained",
            "poorly drained",
            "somewhat poorly drained",
            "moderately well drained",
            "well drained",
            "somewhat excessively drained",
            "excessively drained",
        )
    )
}


def feature_groups(row: ForecastSample, soil: SoilProfile | None) -> dict[str, tuple]:
    """Use the issued trajectory through the verification lead and fixed survey data."""
    run, day = row.run, row.day
    through_lead = run.days[: day.lead_days]
    surfaces = [run.initial_moisture, *(item.surface_moisture for item in through_lead)]
    recent_weights = [exp(-0.7 * (day.lead_days - item.lead_days)) for item in through_lead]
    precipitation = [item.precipitation_mm for item in through_lead]
    et0 = [item.et0_mm for item in through_lead]
    wet_leads = [item.lead_days for item in through_lead if item.precipitation_mm >= 1.0]

    soil_features = (MISSING,) * len(FEATURE_NAMES["soil"])
    if soil is not None:
        capacity, wilting = soil.field_capacity, soil.wilting_point
        available = capacity - wilting if capacity is not None and wilting is not None else MISSING
        soil_features = (
            soil.sand,
            soil.silt,
            soil.clay,
            soil.organic_matter,
            soil.bulk_density,
            capacity,
            wilting,
            available if available > 0 else MISSING,
            _DRAINAGE.get((soil.drainage or "").lower(), MISSING),
            day.surface_moisture - capacity if capacity is not None else MISSING,
            (run.initial_moisture - wilting) / available if available > 0 else MISSING,
            day.precipitation_mm * soil.sand / 100 if soil.sand is not None else MISSING,
        )
    return {
        "v1": forecast_features(run, day),
        "layers": (
            day.surface_moisture - day.subsurface_moisture,
            day.surface_moisture - run.initial_moisture,
            day.surface_moisture - surfaces[-2],
            day.subsurface_moisture - through_lead[0].subsurface_moisture,
            sum(surfaces) / len(surfaces),
            max(surfaces) - min(surfaces),
        ),
        "fluxes": (
            log1p(day.precipitation_mm),
            day.et0_mm,
            sum(weight * rain for weight, rain in zip(recent_weights, precipitation, strict=True)),
            sum(weight * loss for weight, loss in zip(recent_weights, et0, strict=True)),
            sum(precipitation) - sum(et0),
            len(wet_leads),
            day.lead_days - max(wet_leads, default=0),
            day.soil_temperature_c - day.air_temperature_c,
            day.precipitation_mm * (1 - run.initial_moisture),
        ),
        "soil": soil_features,
    }


@dataclass(frozen=True)
class Design:
    samples: tuple[ForecastSample, ...]
    stations: tuple[Station, ...]
    site_codes: np.ndarray
    blocks: np.ndarray
    leads: np.ndarray
    raw: np.ndarray
    initial: np.ndarray
    observed: np.ndarray
    features: dict[str, np.ndarray]

    @classmethod
    def from_samples(
        cls, rows: Iterable[ForecastSample], soils: dict[str, SoilProfile]
    ) -> "Design":
        samples = tuple(rows)
        stations = tuple(sorted({row.station for row in samples}, key=lambda station: station.id))
        codes = {station: index for index, station in enumerate(stations)}
        for station in stations:
            soil = soils.get(station.id)
            if soil is not None and (
                abs(soil.latitude - station.latitude) > 0.0001
                or abs(soil.longitude - station.longitude) > 0.0001
            ):
                raise ValueError(f"Static survey coordinates differ for {station.id}")
        groups = [feature_groups(row, soils.get(row.station.id)) for row in samples]
        arrays = {
            group: np.asarray([features[group] for features in groups], dtype=float).reshape(
                len(samples), len(names)
            )
            for group, names in FEATURE_NAMES.items()
        }
        return cls(
            samples=samples,
            stations=stations,
            site_codes=np.asarray([codes[row.station] for row in samples], dtype=int),
            blocks=np.asarray([row.station.block for row in samples]),
            leads=np.asarray([row.day.lead_days for row in samples], dtype=int),
            raw=np.asarray([row.day.surface_moisture for row in samples]),
            initial=np.asarray([row.run.initial_moisture for row in samples]),
            observed=np.asarray([row.observed for row in samples]),
            features={
                name: np.column_stack([arrays[group] for group in groups])
                for name, groups in FEATURE_SETS.items()
            },
        )


def station_weights(codes: np.ndarray) -> np.ndarray:
    counts = np.bincount(codes)
    return len(codes) / (np.count_nonzero(counts) * counts[codes])


def spatial_splits(
    data: Design, indices: np.ndarray, folds: int
) -> list[tuple[np.ndarray, np.ndarray]]:
    groups = data.blocks[indices]
    n_splits = min(folds, len(set(groups)))
    if n_splits < 2:
        raise ValueError("Geographic evaluation requires at least two distinct blocks")
    too_close = {
        code: {
            index
            for index, station in enumerate(data.stations)
            if station_distance_km(
                data.stations[code].latitude,
                data.stations[code].longitude,
                station.latitude,
                station.longitude,
            )
            < BUFFER_KM
        }
        for code in set(data.site_codes[indices])
    }
    splits = []
    for train, evaluate in GroupKFold(n_splits=n_splits).split(indices, groups=groups):
        testing = indices[evaluate]
        blocked = set.union(*(too_close[code] for code in set(data.site_codes[testing])))
        training = indices[train][~np.isin(data.site_codes[indices[train]], sorted(blocked))]
        if len(set(data.site_codes[training])) < 2:
            raise ValueError("Spatial buffer left fewer than two training stations")
        splits.append((training, testing))
    return splits


@dataclass(frozen=True)
class Candidate:
    family: str
    features: str = "v1"
    parameters: tuple = ()
    strength: float = 1.0
    target: str = "residual"

    @property
    def name(self) -> str:
        tuning = "/".join(map(str, self.parameters))
        return f"{self.family}:{self.features}:{self.target}:{self.strength:g}" + (
            f":{tuning}" if tuning else ""
        )


RAW = Candidate("ifs")
PERSISTENCE = Candidate("persistence")
V1 = Candidate("ridge", parameters=(40,), strength=0.5)


def candidate_grid() -> tuple[Candidate, ...]:
    grid = [RAW, PERSISTENCE, Candidate("bias", strength=0.5), Candidate("bias")]
    for features in FEATURE_SETS:
        grid.extend(
            Candidate("ridge", features, (alpha,), strength)
            for alpha in (4, 40, 400, 4000)
            for strength in (0.5, 1.0)
        )
        grid.extend(
            Candidate("elastic_net", features, params, strength=0.75)
            for params in ((0.001, 0.25), (0.004, 0.75))
        )
        grid.extend(
            Candidate("huber", features, (epsilon,), strength=0.75) for epsilon in (1.25, 1.75)
        )
        grid.extend(
            Candidate("polynomial_ridge", features, (alpha,), strength=0.5) for alpha in (40, 400)
        )
        grid.extend(
            Candidate("boosted_trees", features, params, strength=0.75)
            for params in ((7, 25, 2.0), (15, 50, 1.0))
        )
        grid.append(Candidate("extra_trees", features, (5, 30), strength=0.75))
    for features in ("v1", "dynamic", "all"):
        grid.append(Candidate("ridge", features, (40,), target="direct"))
        grid.append(Candidate("boosted_trees", features, (7, 25, 2.0), target="direct"))
        grid.append(Candidate("extra_trees", features, (8, 12), strength=0.75))
    return tuple(grid)


@dataclass(frozen=True)
class Fold:
    design: Design
    training: np.ndarray
    weights: np.ndarray
    matrices: dict[str, tuple[np.ndarray, np.ndarray]]
    target: np.ndarray
    residual: np.ndarray

    @classmethod
    def fit(cls, design: Design, training: np.ndarray) -> "Fold":
        weights = station_weights(design.site_codes[training])
        matrices = {}
        for name, values in design.features.items():
            imputer = SimpleImputer(strategy="median", keep_empty_features=True)
            imputed = imputer.fit_transform(values[training])
            scaler = StandardScaler().fit(imputed, sample_weight=weights)
            matrices[name] = (
                scaler.transform(imputed),
                scaler.transform(imputer.transform(values)),
            )
        target = design.observed[training]
        return cls(design, training, weights, matrices, target, target - design.raw[training])


def estimator_for(candidate: Candidate):
    family, params = candidate.family, candidate.parameters
    match family:
        case "ridge" | "polynomial_ridge":
            return Ridge(alpha=params[0])
        case "elastic_net":
            return ElasticNet(alpha=params[0], l1_ratio=params[1], max_iter=2500)
        case "huber":
            return HuberRegressor(epsilon=params[0], alpha=0.1, max_iter=500)
        case "boosted_trees":
            return HistGradientBoostingRegressor(
                max_iter=70,
                max_leaf_nodes=params[0],
                min_samples_leaf=params[1],
                l2_regularization=params[2],
                early_stopping=False,
                random_state=SEED,
            )
        case "extra_trees":
            return ExtraTreesRegressor(
                n_estimators=40,
                max_depth=params[0],
                min_samples_leaf=params[1],
                max_features=0.8,
                n_jobs=1,
                random_state=SEED,
            )
        case _:
            raise ValueError(f"Unknown estimator {family}")


def predict(candidate: Candidate, fold: Fold, indices: np.ndarray) -> np.ndarray:
    baseline = fold.design.raw[indices]
    prediction = unbounded_prediction(candidate, fold, indices)
    return np.clip(baseline + candidate.strength * (prediction - baseline), 0, 1)


def unbounded_prediction(candidate: Candidate, fold: Fold, indices: np.ndarray) -> np.ndarray:
    design = fold.design
    baseline = design.raw[indices]
    if candidate.family == "ifs":
        return baseline
    if candidate.family == "persistence":
        return design.initial[indices]
    if candidate.family == "bias":
        return baseline + np.average(fold.residual, weights=fold.weights)
    training, transformed = fold.matrices[candidate.features]
    testing = transformed[indices]
    if candidate.family == "polynomial_ridge":
        polynomial = PolynomialFeatures(degree=2, include_bias=False)
        training = polynomial.fit_transform(training)
        testing = polynomial.transform(testing)
    estimator = estimator_for(candidate)
    target = fold.target if candidate.target == "direct" else fold.residual
    estimator.fit(training, target, sample_weight=fold.weights)
    prediction = estimator.predict(testing)
    return prediction if candidate.target == "direct" else baseline + prediction


def score(design: Design, indices: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    return evaluate_predictions(
        predicted, design.observed[indices], station_weights(design.site_codes[indices])
    )


def normalized_error(metrics: dict, baseline: dict) -> float:
    return sum(metrics[key] / max(baseline[key], 1e-12) for key in ("mae", "rmse")) / 2


def choose_candidate(
    grid: tuple[Candidate, ...], scores: dict[Candidate, dict], *, allow_baseline: bool = True
) -> Candidate:
    ranks = {
        candidate: normalized_error(scores[candidate], scores[RAW])
        for candidate in grid
        if allow_baseline or candidate.family not in ("ifs", "persistence")
    }
    tolerance = min(ranks.values()) * (1 + RANK_TOLERANCE)
    simple = (
        "ifs",
        "persistence",
        "bias",
        "ridge",
        "elastic_net",
        "huber",
        "polynomial_ridge",
        "boosted_trees",
        "extra_trees",
    )
    return min(
        (candidate for candidate, rank in ranks.items() if rank <= tolerance),
        key=lambda candidate: (
            simple.index(candidate.family),
            sum(len(FEATURE_NAMES[group]) for group in FEATURE_SETS[candidate.features]),
            ranks[candidate],
            candidate.name,
        ),
    )


def search(
    data: Design, indices: np.ndarray, grid: tuple[Candidate, ...], folds: int
) -> dict[Candidate, dict]:
    predictions = np.empty((len(grid), len(data.samples)))
    for training, evaluation in spatial_splits(data, indices, folds):
        fold = Fold.fit(data, training)
        baseline = data.raw[evaluation]
        fitted = {}
        for row, candidate in enumerate(grid):
            estimator = replace(candidate, strength=1.0)
            if estimator not in fitted:
                fitted[estimator] = unbounded_prediction(estimator, fold, evaluation)
            predictions[row, evaluation] = np.clip(
                baseline + candidate.strength * (fitted[estimator] - baseline), 0, 1
            )
    return {
        candidate: score(data, indices, predictions[row, indices])
        for row, candidate in enumerate(grid)
    }


def nested_search(data: Design, indices: np.ndarray, grid: tuple[Candidate, ...]) -> dict:
    selected_predictions = np.empty(len(data.samples))
    learned_predictions = np.empty(len(data.samples))
    v1_predictions = np.empty(len(data.samples))
    outer = []
    for training, evaluation in spatial_splits(data, indices, folds=5):
        inner_scores = search(data, training, grid, folds=3)
        chosen = choose_candidate(grid, inner_scores)
        learned = choose_candidate(grid, inner_scores, allow_baseline=False)
        fold = Fold.fit(data, training)
        selected_predictions[evaluation] = predict(chosen, fold, evaluation)
        learned_predictions[evaluation] = (
            selected_predictions[evaluation]
            if chosen == learned
            else predict(learned, fold, evaluation)
        )
        v1_predictions[evaluation] = predict(V1, fold, evaluation)
        outer.append(
            {
                "fit_sites": sorted({data.samples[index].station.id for index in training}),
                "test_sites": sorted({data.samples[index].station.id for index in evaluation}),
                "test_blocks": sorted(set(data.blocks[evaluation])),
                "selected": chosen.name,
                "selected_learned": learned.name,
                "metrics": score(data, evaluation, selected_predictions[evaluation]),
            }
        )
    return {
        "pairs": len(indices),
        "sites": len(set(data.site_codes[indices])),
        "outer_folds": outer,
        "ifs": score(data, indices, data.raw[indices]),
        "persistence": score(data, indices, data.initial[indices]),
        "prior_ridge": score(data, indices, v1_predictions[indices]),
        "selected_procedure": score(data, indices, selected_predictions[indices]),
        "learned_procedure": score(data, indices, learned_predictions[indices]),
    }


def ablations(grid: tuple[Candidate, ...], scores: dict[Candidate, dict]) -> dict:
    by_set = {}
    for name in FEATURE_SETS:
        candidates = [
            candidate
            for candidate in grid
            if candidate.features == name and candidate.family not in ("ifs", "persistence", "bias")
        ]
        selected = min(candidates, key=lambda item: normalized_error(scores[item], scores[RAW]))
        by_set[name] = {"best": selected.name, **scores[selected]}
    return by_set


def transfer_design(fit: Design, testing: Design) -> Design:
    """Use the same columns for fit and prediction without adding test rows to the fit."""
    n_sites = len(fit.stations)
    return Design(
        samples=(*fit.samples, *testing.samples),
        stations=(*fit.stations, *testing.stations),
        site_codes=np.r_[fit.site_codes, n_sites + testing.site_codes],
        blocks=np.r_[fit.blocks, testing.blocks],
        leads=np.r_[fit.leads, testing.leads],
        raw=np.r_[fit.raw, testing.raw],
        initial=np.r_[fit.initial, testing.initial],
        observed=np.r_[fit.observed, testing.observed],
        features={
            group: np.row_stack([fit.features[group], testing.features[group]])
            for group in FEATURE_SETS
        },
    )


def lead_intervals(data: Design, calibration: np.ndarray, predicted: np.ndarray) -> np.ndarray:
    lead = data.leads[calibration]
    error = np.abs(data.observed[calibration] - predicted)
    widths = []
    for value in range(1, HORIZON + 1):
        mask = lead == value
        if not np.any(mask):
            raise ValueError(f"No calibration observations for lead {value}")
        weights = station_weights(data.site_codes[calibration][mask])
        order = np.argsort(error[mask])
        index = np.searchsorted(np.cumsum(weights[order]), NOMINAL_INTERVAL * weights.sum())
        widths.append(max(0.001, error[mask][order[index]]))
    return np.asarray(widths)


def diagnostic(
    fitted: Design,
    testing: Design,
    candidates: tuple[Candidate, ...],
    intervals: dict[Candidate, np.ndarray],
) -> dict:
    if not testing.samples:
        return {"pairs": 0, "stations": 0, "metrics": {}}
    data = transfer_design(fitted, testing)
    training = np.arange(len(fitted.samples))
    evaluation = np.arange(len(fitted.samples), len(data.samples))
    fold = Fold.fit(data, training)
    preds = {candidate.name: predict(candidate, fold, evaluation) for candidate in candidates}

    def report(mask: np.ndarray) -> dict:
        indices = evaluation[mask]
        metrics = {name: score(data, indices, predicted[mask]) for name, predicted in preds.items()}
        for candidate, widths in intervals.items():
            error = np.abs(data.observed[indices] - preds[candidate.name][mask])
            weights = station_weights(data.site_codes[indices])
            metrics[candidate.name]["interval_coverage"] = float(
                np.average(error <= widths[data.leads[indices] - 1], weights=weights)
            )
        return {"pairs": len(indices), "metrics": metrics}

    return {
        **report(np.ones(len(evaluation), dtype=bool)),
        "stations": len(testing.stations),
        "by_lead": {
            str(lead): report(testing.leads == lead) for lead in sorted(set(testing.leads))
        },
        "by_station": {
            station.id: {
                **report(testing.site_codes == index),
                "nearest_fitting_km": min(
                    station_distance_km(
                        station.latitude, station.longitude, other.latitude, other.longitude
                    )
                    for other in fitted.stations
                ),
            }
            for index, station in enumerate(testing.stations)
        },
    }


def issue_requests(stations: dict, start: date, end: date) -> Iterable:
    sites = sorted(stations, key=lambda station: station.id)
    first = start + timedelta(days=(-start.weekday()) % 7)
    for offset in range(0, (end - first).days + 1, 7):
        issue = first + timedelta(days=offset)
        for index in range(0, len(sites), 20):
            group = sites[index : index + 20]
            coordinates = [(station.latitude, station.longitude) for station in group]
            yield issue, group, coordinates


def weather_digest(stations: dict, start: date, end: date, cache: Path) -> str:
    """Fingerprint only the forecast inputs used in this cohort."""
    digest = sha256()
    for issue, _, coordinates in issue_requests(stations, start, end):
        key = sha256(ifs_run_url(coordinates, issue, None).encode()).hexdigest()
        raw = (cache / f"{key}.json").read_bytes()
        digest.update(key.encode())
        digest.update(sha256(raw).digest())
    return digest.hexdigest()


def source_digest(path: Traversable) -> str:
    return sha256(path.read_bytes()).hexdigest()


def cached_postcycle_samples(
    observations: dict[Station, dict[date, float]],
    window: tuple[date, date],
    cache: Path,
    root_cache: Path | None,
) -> tuple[list[ForecastSample], dict]:
    rows = []
    source_hashes = {}
    expected = Counter()
    available = Counter()

    def read(group, coordinates, issue, directory, url):
        key = sha256(url(coordinates, issue, None).encode()).hexdigest()
        path = directory / f"{key}.json"
        if not path.is_file():
            return False
        raw = path.read_bytes()
        source_hashes[key] = sha256(raw).hexdigest()
        runs = parse_ifs_runs(json.loads(raw), coordinates, issue)
        for site, run in zip(group, runs, strict=True):
            values = observations[site]
            available[site.id] += 1
            rows.extend(
                ForecastSample(site, run, day, values[day.date])
                for day in run.days
                if day.soil_temperature_c >= 2 and day.date in values
            )
        return True

    for issue, stations, locations in issue_requests(observations, *window):
        expected.update(site.id for site in stations)
        if read(stations, locations, issue, cache, ifs_run_url) or root_cache is None:
            continue
        for site, coords in zip(stations, locations, strict=True):
            read([site], [coords], issue, root_cache, root_zone_run_url)
    return rows, {
        "weather_sources_sha256": source_hashes,
        "weather_issues": {
            site: {"available": available[site], "expected": count}
            for site, count in expected.items()
        },
    }


def postcycle_diagnostic(
    observations_path: Path,
    soils: dict[str, SoilProfile],
    cache: Path,
    root_cache: Path | None,
    download: bool,
    earlier_sites: set[Station],
    earlier_evaluation: set[Station],
    development: Design,
    candidates: tuple[Candidate, ...],
    intervals: dict[Candidate, np.ndarray],
) -> dict:
    protocol = json.loads(POSTCYCLE_PROTOCOL.read_bytes())
    manifest = json.loads(observations_path.with_suffix(".manifest.json").read_bytes())
    if manifest["protocol_sha256"] != source_digest(POSTCYCLE_PROTOCOL) or manifest[
        "csv_sha256"
    ] != source_digest(observations_path):
        raise ValueError("50R1 evaluation does not match its sealed source manifest")
    reserved = {site["station_id"]: site for site in protocol["sites"]}
    observations = read_observations(observations_path)
    for station in observations:
        item = reserved.get(station.id)
        if (
            item is None
            or (station.latitude, station.longitude) != (item["latitude"], item["longitude"])
            or not separate_from(station, earlier_sites)
            or any(
                station.id == other.id
                or station_distance_km(
                    station.latitude, station.longitude, other.latitude, other.longitude
                )
                < BUFFER_KM
                for other in earlier_evaluation
            )
        ):
            raise ValueError(f"Unsealed or previously exposed 50R1 station {station.id}")

    issue_window = tuple(date.fromisoformat(protocol[key]) for key in ("first_issue", "last_issue"))
    eligible = {
        station: days for station, days in observations.items() if len(days) >= MIN_TEST_PAIRS
    }
    if eligible and download:
        load_period(eligible, *issue_window, cache)
    rows, archive = cached_postcycle_samples(eligible, issue_window, cache, root_cache)
    if any(row.run.model_cycle != protocol["model_cycle"] for row in rows):
        raise ValueError("Unexpected IFS cycle in post-upgrade evaluation")
    design = Design.from_samples(rows, soils)
    overall = diagnostic(development, design, candidates, intervals)
    support = {
        key
        for key, value in overall.get("by_station", {}).items()
        if value["pairs"] >= MIN_TEST_PAIRS and value["nearest_fitting_km"] <= SERVING_RADIUS_KM
    }
    in_support = Design.from_samples((row for row in rows if row.station.id in support), soils)
    return {
        "cycle": protocol["model_cycle"],
        "observations": manifest,
        **archive,
        "forecast_verification_sites_with_survey": sorted(
            station.id for station in design.stations if station.id in soils
        ),
        "all_research_probes": overall,
        "qualified_in_support_probes": diagnostic(development, in_support, candidates, intervals),
        "verified_managed_farms_in_support": sorted(
            key for key in support if reserved[key]["managed_crop_verified"]
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--known-farms", type=Path, required=True)
    parser.add_argument("--soils", type=Path, required=True)
    parser.add_argument("--known-farm-soils", type=Path, required=True)
    parser.add_argument("--postcycle-observations", type=Path)
    parser.add_argument("--postcycle-soils", type=Path)
    parser.add_argument("--postcycle-root-cache", type=Path)
    parser.add_argument("--download-postcycle-weather", action="store_true")
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    v1 = get_moisture_model()
    roles = v1.validation["station_splits"]
    fit_ids = {site["station_id"] for site in roles["train"]}
    calibration_ids = {site["station_id"] for site in roles["calibration"]}
    soil = read_soils(args.soils)
    observations = {
        site: days
        for site, days in read_observations(args.observations).items()
        if site.id in fit_ids | calibration_ids
    }
    development_rows = load_period(observations, *DEVELOPMENT_WINDOW, args.cache)
    development = Design.from_samples(
        (row for row in development_rows if row.station.id in fit_ids), soil
    )
    calibration = Design.from_samples(
        (row for row in development_rows if row.station.id in calibration_ids), soil
    )
    grid = candidate_grid()
    indices = np.arange(len(development.samples))
    with threadpool_limits(limits=1):
        internal_scores = search(development, indices, grid, folds=5)
        final = choose_candidate(grid, internal_scores)
        learned = choose_candidate(grid, internal_scores, allow_baseline=False)
        nested = nested_search(development, indices, grid)
        candidates = tuple(dict.fromkeys((RAW, PERSISTENCE, V1, final, learned)))

        calibration_design = transfer_design(development, calibration)
        calibration_fold = Fold.fit(calibration_design, indices)
        calibration_index = np.arange(len(indices), len(calibration_design.samples))
        intervals = {
            candidate: lead_intervals(
                calibration_design,
                calibration_index,
                predict(candidate, calibration_fold, calibration_index),
            )
            for candidate in candidates
            if candidate.family not in ("ifs", "persistence")
        }

        known_farms = read_observations(args.known_farms)
        known = Design.from_samples(
            load_period(known_farms, *KNOWN_FARM_WINDOW, args.cache),
            read_soils(args.known_farm_soils),
        )
        historical = diagnostic(development, known, candidates, intervals)
        postcycle = (
            postcycle_diagnostic(
                args.postcycle_observations,
                read_soils(args.postcycle_soils) if args.postcycle_soils else {},
                args.cache,
                args.postcycle_root_cache,
                args.download_postcycle_weather,
                {site for site in observations if site.id in fit_ids | calibration_ids},
                set(known_farms)
                | {
                    Station(
                        item["station_id"],
                        item["latitude"],
                        item["longitude"],
                        item.get("network", ""),
                        item.get("location", ""),
                    )
                    for role, items in roles.items()
                    if role not in ("train", "calibration")
                    for item in items
                },
                development,
                candidates,
                intervals,
            )
            if args.postcycle_observations
            else None
        )

    report = {
        "schema_version": 1,
        "target": "daily volumetric soil moisture around 5 cm in m³/m³",
        "model_cycle_development": sorted({row.run.model_cycle for row in development.samples}),
        "operational_cycle": CURRENT_MODEL_CYCLE,
        "versions": {"numpy": np.__version__, "scikit_learn": sklearn_version},
        "thread_limit": 1,
        "seed": SEED,
        "sources_sha256": {
            "development_observations": source_digest(args.observations),
            "known_farm_observations": source_digest(args.known_farms),
            "development_soil": source_digest(args.soils),
            "known_farm_soil": source_digest(args.known_farm_soils),
            "development_ifs": weather_digest(observations, *DEVELOPMENT_WINDOW, args.cache),
            "known_farm_ifs": weather_digest(known_farms, *KNOWN_FARM_WINDOW, args.cache),
            "postcycle_soil": source_digest(args.postcycle_soils) if args.postcycle_soils else None,
        },
        "candidate_count": len(grid),
        "feature_groups": FEATURE_NAMES,
        "feature_sets": FEATURE_SETS,
        "selected": final.name,
        "selected_learned": learned.name,
        "fold_buffer_km": BUFFER_KM,
        "nested_development": nested,
        "non_nested_ablation": ablations(grid, internal_scores),
        "full_development_search": {
            candidate.name: {**asdict(candidate), "metrics": internal_scores[candidate]}
            for candidate in grid
        },
        "calibration": {
            "pairs": len(calibration.samples),
            "stations": len(calibration.stations),
            "interval_half_width": {
                candidate.name: width.tolist() for candidate, width in intervals.items()
            },
        },
        "previously_scored_ifc": historical,
        "post_upgrade_diagnostic": postcycle,
        "promoted": False,
        "promotion_blocker": (
            "The IFC comparison was previously scored on 49R1. "
            "The 50R1 research probes are not a verified managed-farm holdout."
        ),
    }
    write_atomically(
        args.output, json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )


if __name__ == "__main__":
    main()
