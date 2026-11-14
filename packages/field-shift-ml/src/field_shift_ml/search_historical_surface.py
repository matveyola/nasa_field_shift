"""Nested geographical search for an offline correction to historical ERA5-Land moisture."""

import argparse
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from itertools import combinations, product
from pathlib import Path

import numpy as np
from sklearn import __version__ as sklearn_version
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.kernel_approximation import RBFSampler
from sklearn.linear_model import ElasticNet, Ridge
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from field_shift.modules.geo_ml.moisture_model import station_distance_km
from field_shift_ml.historical_surface_features import StationRows, prepare_rows
from field_shift_ml.train_moisture import (
    Station,
    read_observations,
    separate_from,
    write_atomically,
)
from field_shift_ml.train_moisture_v2 import FIT_END, digest, read_soils

ARTIFACT = Path("src/field_shift/modules/geo_ml/artifacts/moisture-v2.json")
BASE_GROUPS = ("raw", "land_history")
EXTRA_GROUPS = ("weather", "soil", "terrain", "geography")
COMPLEXITY = ("raw", "bias", "ridge", "robust_ridge", "elastic_net", "rbf", "boosting", "trees")
SEED = 101


@dataclass(frozen=True, slots=True)
class Recipe:
    family: str
    groups: tuple[str, ...]
    parameters: tuple[float, ...] = ()
    strength: float = 1.0

    @property
    def name(self) -> str:
        params = "/".join(f"{value:g}" for value in (*self.parameters, self.strength))
        return f"{self.family}:{'+'.join(self.groups)}:{params}"


RAW = Recipe("raw", ("raw",))
BIAS = Recipe("bias", ("raw",), strength=0.5)


def recipe_grid() -> tuple[Recipe, ...]:
    all_groups = [
        (*BASE_GROUPS, *extra)
        for length in range(len(EXTRA_GROUPS) + 1)
        for extra in combinations(EXTRA_GROUPS, length)
    ]
    regularized = [
        Recipe("ridge", groups, (alpha,), strength)
        for groups, alpha, strength in product([("raw",), *all_groups], (30, 300, 3000), (0.5, 1.0))
    ]
    richer = [
        BASE_GROUPS,
        (*BASE_GROUPS, "weather"),
        (*BASE_GROUPS, "soil"),
        (*BASE_GROUPS, "weather", "soil"),
        (*BASE_GROUPS, "weather", "soil", "terrain"),
        all_groups[-1],
    ]
    nonlinear = {
        "robust_ridge": ((100, 0.04), (1000, 0.04)),
        "elastic_net": ((0.001, 0.2), (0.004, 0.2), (0.004, 0.8)),
        "rbf": ((0.3, 300), (1.0, 300)),
        "boosting": ((50, 7, 4), (90, 7, 4), (90, 15, 12)),
        "trees": ((3, 100), (6, 150)),
    }
    return (
        RAW,
        BIAS,
        *regularized,
        *(
            Recipe(family, groups, params, strength)
            for groups in richer
            for family, parameters in nonlinear.items()
            for params, strength in product(parameters, (0.5, 1.0))
        ),
    )


class Dataset:
    def __init__(self, rows: list[StationRows]) -> None:
        self.stations = [row.station for row in rows]
        self.site = np.repeat(np.arange(len(rows)), [len(row.dates) for row in rows])
        self.blocks = np.asarray([station.block for station in self.stations])[self.site]
        self.dates = np.asarray([day for row in rows for day in row.dates], dtype="datetime64[D]")
        self.actual = np.concatenate([row.actual for row in rows])
        self.groups = {group: tuple(columns) for group, columns in rows[0].groups.items()}
        self.values = {
            group: np.column_stack(
                [np.concatenate([row.groups[group][column] for row in rows]) for column in columns]
            )
            for group, columns in self.groups.items()
        }
        self.raw = self.values["raw"][:, 0]

    def matrix(self, groups: tuple[str, ...]) -> np.ndarray:
        if {"soil", "weather"}.issubset(groups):
            groups = (*groups, "soil_weather")
        return np.column_stack([self.values[group] for group in groups])

    def weights(self, indices: np.ndarray) -> np.ndarray:
        sites = self.site[indices]
        return 1.0 / np.bincount(sites)[sites]

    def cohort(self, ids: Iterable[str], start: date, end: date) -> np.ndarray:
        accepted = set(ids)
        wanted = np.asarray([station.id in accepted for station in self.stations])
        return np.flatnonzero(wanted[self.site] & (self.dates >= start) & (self.dates <= end))

    def splits(self, indices: np.ndarray, folds: int) -> list[tuple[np.ndarray, np.ndarray]]:
        n = len(set(self.blocks[indices]))
        if n < 3:
            raise ValueError("Need at least three geographic blocks")
        result = []
        for train, validation in GroupKFold(min(n, folds)).split(
            indices, groups=self.blocks[indices]
        ):
            hold = indices[validation]
            held_sites = {self.stations[i] for i in np.unique(self.site[hold])}
            allowed = np.asarray([separate_from(station, held_sites) for station in self.stations])
            fit = indices[train][allowed[self.site[indices[train]]]]
            if not len(fit) or len(set(self.blocks[fit])) < 2:
                raise ValueError("Geographic buffer removed too many fitting blocks")
            result.append((fit, hold))
        return result


def design(matrix: np.ndarray, fit: np.ndarray, hold: np.ndarray, weights: np.ndarray):
    imputer = SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)
    train = imputer.fit_transform(matrix[fit])
    scaler = StandardScaler().fit(train, sample_weight=weights)
    return scaler.transform(train), scaler.transform(imputer.transform(matrix[hold]))


def correction(recipe: Recipe, train, hold, residual, weights) -> np.ndarray:
    family, parameters = recipe.family, recipe.parameters
    if family == "raw":
        return np.zeros(len(hold))
    if family == "bias":
        return np.full(len(hold), np.average(residual, weights=weights))
    if family == "robust_ridge":
        alpha, cutoff = parameters
        weight = weights
        for _ in range(4):
            estimator = Ridge(alpha=alpha).fit(train, residual, sample_weight=weight)
            errors = np.abs(estimator.predict(train) - residual)
            weight = weights * np.minimum(1, cutoff / np.maximum(errors, 1e-9))
        return estimator.predict(hold)
    if family == "rbf":
        bandwidth, alpha = parameters
        kernel = RBFSampler(gamma=bandwidth / train.shape[1], n_components=36, random_state=SEED)
        train, hold = kernel.fit_transform(train), kernel.transform(hold)
        estimator = Ridge(alpha=alpha)
    elif family == "ridge":
        estimator = Ridge(alpha=parameters[0])
    elif family == "elastic_net":
        estimator = ElasticNet(alpha=parameters[0], l1_ratio=parameters[1], max_iter=2000)
    elif family == "boosting":
        iterations, leaves, regularization = parameters
        estimator = HistGradientBoostingRegressor(
            max_iter=iterations,
            max_leaf_nodes=leaves,
            min_samples_leaf=100,
            l2_regularization=regularization,
            learning_rate=0.06,
            early_stopping=False,
            random_state=SEED,
        )
    elif family == "trees":
        depth, minimum_leaf = parameters
        estimator = ExtraTreesRegressor(
            n_estimators=28,
            max_depth=depth,
            min_samples_leaf=minimum_leaf,
            max_features=0.7,
            n_jobs=1,
            random_state=SEED,
        )
    else:
        raise ValueError(f"Unknown estimator family {family}")
    return estimator.fit(train, residual, sample_weight=weights).predict(hold)


def predictions(data: Dataset, fit: np.ndarray, hold: np.ndarray, recipes: Iterable[Recipe]):
    by_group = defaultdict(list)
    for recipe in recipes:
        by_group[recipe.groups].append(recipe)
    weights = data.weights(fit)
    weights *= len(fit) / weights.sum()
    residual = data.actual[fit] - data.raw[fit]
    for groups, candidates in by_group.items():
        train, validation = design(data.matrix(groups), fit, hold, weights)
        memo = {}
        for recipe in candidates:
            key = (recipe.family, recipe.parameters)
            if key not in memo:
                memo[key] = correction(recipe, train, validation, residual, weights)
            yield recipe, np.clip(data.raw[hold] + recipe.strength * memo[key], 0, 1)


def moments(predicted, actual, weights) -> np.ndarray:
    error = predicted - actual
    return np.asarray(
        (weights @ np.abs(error), weights @ (error**2), weights @ error, weights.sum())
    )


def summarize(totals) -> dict[str, float]:
    absolute, square, signed, weight = totals
    return {
        "mae": float(absolute / weight),
        "rmse": float(np.sqrt(square / weight)),
        "bias": float(signed / weight),
    }


def cross_validate(data: Dataset, splits, recipes: tuple[Recipe, ...]) -> dict[Recipe, dict]:
    totals = {recipe: np.zeros(4) for recipe in recipes}
    for fit, hold in splits:
        actual, weights = data.actual[hold], data.weights(hold)
        for recipe, estimate in predictions(data, fit, hold, recipes):
            totals[recipe] += moments(estimate, actual, weights)
    return {recipe: summarize(value) for recipe, value in totals.items()}


def score(metrics: dict, raw: dict) -> float:
    return sum(metrics[key] / max(raw[key], 1e-12) for key in ("mae", "rmse")) / 2


def choose(scores: dict[Recipe, dict], *, learned_only: bool = False) -> Recipe:
    ranked = {
        recipe: score(metrics, scores[RAW])
        for recipe, metrics in scores.items()
        if not learned_only or recipe.family not in ("raw", "bias")
    }
    cutoff = min(ranked.values()) + 0.005
    return min(
        (recipe for recipe, value in ranked.items() if value <= cutoff),
        key=lambda recipe: (
            COMPLEXITY.index(recipe.family),
            len(recipe.groups),
            ranked[recipe],
            recipe.name,
        ),
    )


def nested_scores(data: Dataset, outer, recipes) -> tuple[dict, list[dict]]:
    totals = {name: np.zeros(4) for name in ("era5_land", "selected")}
    selections = []
    for fit, hold in outer:
        selected = choose(cross_validate(data, data.splits(fit, 3), recipes))
        weights = data.weights(hold)
        totals["era5_land"] += moments(data.raw[hold], data.actual[hold], weights)
        [(_, estimate)] = predictions(data, fit, hold, (selected,))
        totals["selected"] += moments(estimate, data.actual[hold], weights)
        selections.append(
            {
                "recipe": selected.name,
                "fit_sites": sorted(data.stations[i].id for i in np.unique(data.site[fit])),
                "held_sites": sorted(data.stations[i].id for i in np.unique(data.site[hold])),
                "pairs": len(hold),
                "raw": summarize(moments(data.raw[hold], data.actual[hold], weights)),
                "selected": summarize(moments(estimate, data.actual[hold], weights)),
            }
        )
        print(f"Outer fold {len(selections)}: {selected.name}", flush=True)
    return {name: summarize(value) for name, value in totals.items()}, selections


def external_diagnostics(data: Dataset, training, calibration, diagnostics, learned):
    results = {}
    [(_, estimate)] = predictions(data, training, calibration, (learned,))
    weights = data.weights(calibration)
    errors = np.abs(estimate - data.actual[calibration])
    order = np.argsort(errors)
    position = np.searchsorted(np.cumsum(weights[order]), 0.90 * weights.sum())
    width = float(errors[order[position]])
    for name, hold in diagnostics.items():
        [(_, estimate)] = predictions(data, training, hold, (learned,))
        weights = data.weights(hold)
        results[name] = {
            "pairs": len(hold),
            "sites": len(set(data.site[hold])),
            "era5_land": summarize(moments(data.raw[hold], data.actual[hold], weights)),
            "learned": summarize(moments(estimate, data.actual[hold], weights)),
            "interval_coverage": float(
                np.average(np.abs(estimate - data.actual[hold]) <= width, weights=weights)
            ),
            "by_site": {
                data.stations[site].id: {
                    "pairs": int(np.sum(mask)),
                    "raw": summarize(
                        moments(data.raw[hold][mask], data.actual[hold][mask], weights[mask])
                    ),
                    "learned": summarize(
                        moments(estimate[mask], data.actual[hold][mask], weights[mask])
                    ),
                }
                for site in np.unique(data.site[hold])
                if (mask := data.site[hold] == site).any()
            },
        }
    return {"interval_half_width": width, "known_diagnostics": results}


def observations_for(paths: argparse.Namespace):
    artifact = json.loads(ARTIFACT.read_bytes())
    pinned_sites = {
        site["station_id"]: (site["latitude"], site["longitude"])
        for stations in artifact["validation"]["station_splits"].values()
        for site in stations
    }
    roles = {
        role: {site["station_id"] for site in stations}
        for role, stations in artifact["validation"]["station_splits"].items()
    }
    known = set.union(*roles.values())
    main = {
        site: days
        for site, days in read_observations(paths.observations).items()
        if site.id in known
    }
    iowa = read_observations(paths.iowa)
    if known.intersection(site.id for site in iowa):
        raise ValueError("Known Iowa diagnostic overlaps another station role")
    if {station.id for station in main} != known:
        raise ValueError("The historical observation snapshot is missing pinned stations")
    if any((station.latitude, station.longitude) != pinned_sites[station.id] for station in main):
        raise ValueError("The historical observation snapshot moved a pinned station")
    roles["known_iowa"] = {site.id for site in iowa}
    return main | iowa, roles


def matched_weather_diagnostic(
    rows: list[StationRows], train_sites: set[str], recipe: Recipe
) -> dict:
    """Measure the weather group with identical stations and model settings on both sides."""
    data = Dataset(rows)
    available = {
        row.station.id for row in rows if row.digests["era5"] != "not_cached"
    } & train_sites
    training = data.cohort(available, date(2021, 1, 1), FIT_END)
    without = replace(recipe, groups=tuple(group for group in recipe.groups if group != "weather"))
    with_weather = replace(
        without,
        groups=tuple(
            group
            for group in (*BASE_GROUPS, *EXTRA_GROUPS)
            if group == "weather" or group in without.groups
        ),
    )
    metrics = cross_validate(data, data.splits(training, 4), (RAW, without, with_weather))
    return {
        "sites": len(available),
        "pairs": len(training),
        "era5_land": metrics[RAW],
        "with_weather": metrics[with_weather],
        "without_weather": metrics[without],
    }


@dataclass(frozen=True, slots=True)
class SupportRule:
    strength: float
    radius_km: int | None

    def predict(self, raw: np.ndarray, estimate: np.ndarray, distance: np.ndarray) -> np.ndarray:
        taper = 1 if self.radius_km is None else np.maximum(0, 1 - distance / self.radius_km)
        return raw + self.strength * taper * (estimate - raw)


BASELINE_SUPPORT = SupportRule(0, None)
SUPPORT_RULES = tuple(
    SupportRule(strength, radius)
    for strength, radius in product((0.25, 0.5, 0.75, 1), (180, 300, 450, None))
)


def fold_support(data: Dataset, fit, hold, recipe: Recipe) -> tuple[np.ndarray, np.ndarray]:
    [(_, estimate)] = predictions(data, fit, hold, (recipe,))
    fitting = [data.stations[site] for site in np.unique(data.site[fit])]
    distance = np.zeros(len(data.stations))
    for site in np.unique(data.site[hold]):
        station = data.stations[site]
        distance[site] = min(
            station_distance_km(
                station.latitude, station.longitude, other.latitude, other.longitude
            )
            for other in fitting
        )
    return estimate, distance[data.site[hold]]


def select_support(data: Dataset, splits, recipe: Recipe) -> SupportRule:
    held = np.concatenate([hold for _, hold in splits])
    evaluations = [fold_support(data, fit, hold, recipe) for fit, hold in splits]
    estimate, distance = (np.concatenate(values) for values in zip(*evaluations, strict=True))
    raw, actual, weights = data.raw[held], data.actual[held], data.weights(held)
    baseline = summarize(moments(raw, actual, weights))
    site = data.site[held]
    baseline_squared_error = np.bincount(site, weights=(raw - actual) ** 2)
    eligible = {}
    for rule in SUPPORT_RULES:
        corrected = rule.predict(raw, estimate, distance)
        metrics = summarize(moments(corrected, actual, weights))
        site_squared_error = np.bincount(site, weights=(corrected - actual) ** 2)
        if all(metrics[key] <= baseline[key] * 0.95 for key in ("mae", "rmse")) and np.all(
            site_squared_error <= baseline_squared_error * (1.10**2) + 1e-12
        ):
            eligible[rule] = score(metrics, baseline)
    return min(eligible, key=eligible.__getitem__) if eligible else BASELINE_SUPPORT


def known_cohorts(data: Dataset, roles: dict) -> dict[str, np.ndarray]:
    start, end = date(2025, 1, 1), date(2025, 12, 31)
    iowa = data.cohort(roles["known_iowa"], start, end)
    counts = np.bincount(data.site[iowa])
    return {
        "regional_2025": data.cohort(roles["regional"], start, end),
        "iowa_2025_previously_seen": iowa[counts[data.site[iowa]] >= 30],
    }


def support_diagnostic(rows: list[StationRows], roles: dict, frozen: dict, recipe: Recipe) -> dict:
    """Tune distance and correction strength on the same inner sites as each locked fold."""
    data = Dataset(rows)
    training = data.cohort(roles["train"], date(2021, 1, 1), FIT_END)
    outer = data.splits(training, 4)
    recipes = {candidate.name: candidate for candidate in recipe_grid()}
    totals = {name: np.zeros(4) for name in ("era5_land", "original", "support_limited")}
    folds = []
    for (fit, hold), frozen_fold in zip(outer, frozen["outer_folds"], strict=True):
        fit_sites = sorted(data.stations[i].id for i in np.unique(data.site[fit]))
        held_sites = sorted(data.stations[i].id for i in np.unique(data.site[hold]))
        if (fit_sites, held_sites) != (frozen_fold["fit_sites"], frozen_fold["held_sites"]):
            raise ValueError("Frozen and replayed geographic folds disagree")
        local_recipe = recipes[frozen_fold["recipe"]]
        rule = select_support(data, data.splits(fit, 3), local_recipe)
        estimate, distance = fold_support(data, fit, hold, local_recipe)
        raw, actual, weights = data.raw[hold], data.actual[hold], data.weights(hold)
        totals["era5_land"] += moments(raw, actual, weights)
        totals["original"] += moments(estimate, actual, weights)
        totals["support_limited"] += moments(rule.predict(raw, estimate, distance), actual, weights)
        folds.append({"held_sites": held_sites, "rule": asdict(rule)})

    selected = select_support(data, outer, recipe)
    known = {}
    for name, hold in known_cohorts(data, roles).items():
        estimate, distance = fold_support(data, training, hold, recipe)
        raw, actual, weights = data.raw[hold], data.actual[hold], data.weights(hold)
        known[name] = {
            "pairs": len(hold),
            "era5_land": summarize(moments(raw, actual, weights)),
            "original": summarize(moments(estimate, actual, weights)),
            "support_limited": summarize(
                moments(selected.predict(raw, estimate, distance), actual, weights)
            ),
        }
    return {
        "rule_count": len(SUPPORT_RULES),
        "development_folds": folds,
        "nested": {name: summarize(values) for name, values in totals.items()},
        "selected_rule": asdict(selected),
        "known_diagnostics": known,
    }


def fresh_diagnostic(args: argparse.Namespace) -> dict:
    """Refit the previously selected recipe and score it on later research probes."""
    frozen = json.loads(args.frozen_report.read_bytes())
    previous, roles = observations_for(args)
    soils = read_soils(args.soil) | read_soils(args.iowa_soil)
    expected = {
        "observations": digest(args.observations),
        "soil": digest(args.soil),
        "iowa": digest(args.iowa),
        "iowa_soil": digest(args.iowa_soil),
        "roles_artifact": digest(ARTIFACT),
    }
    if any(frozen["input_hashes"][name] != value for name, value in expected.items()):
        raise ValueError("Fitting inputs differ from the frozen selection report")
    if frozen["sklearn_version"] != sklearn_version:
        raise ValueError("Use the scikit-learn version that selected the frozen recipe")
    fresh = read_observations(args.fresh_observations)
    manifest = json.loads(args.fresh_manifest.read_bytes())
    registered = {site["station_id"]: site for site in manifest["sites"]}
    first_day = date.fromisoformat(manifest["first_issue"]) + timedelta(days=1)
    last_day = date.fromisoformat(manifest["last_issue"]) + timedelta(days=5)
    original = json.loads(ARTIFACT.with_name("moisture-v1.json").read_bytes())
    old_stations = set(previous) | {
        Station(site["station_id"], site["latitude"], site["longitude"], "", "")
        for cohort in original["validation"]["station_splits"].values()
        for site in cohort
    }
    old = {station.id for station in old_stations}
    qualified = {}
    excluded = {}
    for station, days in fresh.items():
        record = registered.get(station.id)
        if not record or (station.latitude, station.longitude) != (
            record["latitude"],
            record["longitude"],
        ):
            raise ValueError(f"Unregistered research station {station.id}")
        if station.id in old or not separate_from(station, old_stations):
            excluded[station.id] = "historically exposed or inside the geographic buffer"
        elif len(days) < 30:
            excluded[station.id] = "fewer than 30 observed days"
        elif min(days) < first_day or max(days) > last_day:
            raise ValueError(f"Unexpected diagnostic period for {station.id}")
        else:
            qualified[station] = days
    for station_id in set(registered) - {station.id for station in fresh}:
        excluded[station_id] = "no quality-controlled days in the source snapshot"
    rows, terrain = prepare_rows(
        previous,
        soils,
        args.cache,
        args.external_cache,
        fetch=False,
        allow_missing_weather=frozen["optional_weather_cache"],
    )
    if terrain != frozen["input_hashes"]["terrain"] or any(
        row.digests != frozen["input_hashes"]["stations"][row.station.id] for row in rows
    ):
        raise ValueError("A predictor cache changed since selection")
    payload = frozen["selected_learned"]
    recipe = Recipe(
        payload["family"],
        tuple(payload["groups"]),
        tuple(payload["parameters"]),
        payload["strength"],
    )
    report = {
        "frozen_report_sha256": digest(args.frozen_report),
        "frozen_recipe": recipe.name,
        "observations_sha256": digest(args.fresh_observations),
        "site_manifest_sha256": digest(args.fresh_manifest),
        "matched_weather_diagnostic": matched_weather_diagnostic(rows, roles["train"], recipe),
        "geographic_support_diagnostic": support_diagnostic(rows, roles, frozen, recipe),
        "fresh_independent_managed_farms": 0,
        "excluded": excluded,
        "promoted": False,
    }
    if not qualified:
        return report
    fresh_soils = read_soils(args.fresh_soil)
    try:
        new_rows, new_terrain = prepare_rows(
            qualified,
            fresh_soils,
            args.cache,
            args.external_cache,
            fetch=args.fetch_missing,
            end_at=last_day,
            allow_missing_weather=args.allow_missing_weather,
        )
    except FileNotFoundError:
        return {
            **report,
            "unscored_sites": sorted(station.id for station in qualified),
            "source_availability": "incomplete_era5_or_terrain_cache",
        }
    rows_joined = rows + new_rows
    data = Dataset(rows_joined)
    earliest = date(2021, 1, 1)
    training = data.cohort(roles["train"], earliest, FIT_END)
    calibration = data.cohort(roles["calibration"], earliest, FIT_END)
    hold = data.cohort((station.id for station in qualified), date(2026, 1, 1), date(2026, 12, 31))
    counts = np.bincount(data.site[hold], minlength=len(data.stations))
    eligible_sites = counts >= 30
    for site, station in enumerate(data.stations):
        if station in qualified and counts[site] < 30:
            excluded[station.id] = "fewer than 30 thawed pairs"
        elif (
            station in qualified
            and "weather" in recipe.groups
            and rows_joined[site].digests["era5"] == "not_cached"
        ):
            excluded[station.id] = "selected recipe requires the uncached weather group"
            eligible_sites[site] = False
        elif station in qualified and "soil" in recipe.groups and station.id not in fresh_soils:
            excluded[station.id] = "selected recipe requires a soil profile"
            eligible_sites[site] = False
    hold = hold[eligible_sites[data.site[hold]]]
    fitting_sites = [data.stations[i] for i in np.unique(data.site[training])]
    sites = {}
    for site in np.unique(data.site[hold]):
        station = data.stations[site]
        nearest = min(
            station_distance_km(
                station.latitude, station.longitude, other.latitude, other.longitude
            )
            for other in fitting_sites
        )
        sites[station.id] = {
            "distance_to_fit_km": nearest,
            "inside_previous_radius": nearest <= 180,
            "verified_managed_crop": registered[station.id]["managed_crop_verified"],
            "pairs": int(counts[site]),
        }
    return {
        **report,
        "data_hashes": {
            "observations": digest(args.fresh_observations),
            "soil": digest(args.fresh_soil),
            "site_manifest": digest(args.fresh_manifest),
            "terrain": new_terrain,
            "weather": {row.station.id: row.digests for row in new_rows},
        },
        "scores": external_diagnostics(
            data, training, calibration, {"research_probes_2026": hold}, recipe
        )
        if len(hold)
        else {},
        "sites": sites,
        "fresh_independent_managed_farms": sum(
            site["verified_managed_crop"] and site["inside_previous_radius"]
            for site in sites.values()
        ),
    }


def run(args: argparse.Namespace) -> dict:
    observations, roles = observations_for(args)
    soils = read_soils(args.soil) | read_soils(args.iowa_soil)
    rows, terrain = prepare_rows(
        observations,
        soils,
        args.cache,
        args.external_cache,
        fetch=args.fetch_missing,
        allow_missing_weather=args.allow_missing_weather,
    )
    data = Dataset(rows)
    start = date(2021, 1, 1)
    training = data.cohort(roles["train"], start, FIT_END)
    missing_weather = [row.station.id for row in rows if row.digests["era5"] == "not_cached"]
    if len(roles["train"].intersection(missing_weather)) > len(roles["train"]) / 3:
        raise ValueError("Optional weather is missing for more than one third of the fitting sites")
    calibration = data.cohort(roles["calibration"], start, FIT_END)
    diagnostics = known_cohorts(data, roles)
    recipes = recipe_grid()
    outer = data.splits(training, 4)
    print(f"{len(recipes)} recipes on {len(training)} fitting pairs", flush=True)
    nested, folds = nested_scores(data, outer, recipes)
    selection_scores = cross_validate(data, outer, recipes)
    selected = choose(selection_scores)
    learned = choose(selection_scores, learned_only=True)
    ablations = tuple(
        replace(learned, groups=tuple(item for item in learned.groups if item != group))
        for group in learned.groups
        if group != "raw"
    )
    ablation_scores = cross_validate(data, outer, (learned, *ablations))
    return {
        "sklearn_version": sklearn_version,
        "fit_end": FIT_END.isoformat(),
        "training_pairs": len(training),
        "training_sites": len(set(data.site[training])),
        "calibration_pairs": len(calibration),
        "configuration_count": len(recipes),
        "optional_weather_cache": args.allow_missing_weather,
        "stations_missing_optional_weather": missing_weather,
        "feature_columns": data.groups,
        "missing_training_fraction": {
            group: dict(
                zip(
                    data.groups[group],
                    np.isnan(matrix[training]).mean(axis=0).tolist(),
                    strict=True,
                )
            )
            for group, matrix in data.values.items()
        },
        "nested": nested,
        "outer_folds": folds,
        "selection": asdict(selected),
        "selected_learned": asdict(learned),
        "selection_scores": {recipe.name: values for recipe, values in selection_scores.items()},
        "development_fixed_recipe_ablation": {
            recipe.name: values for recipe, values in ablation_scores.items()
        },
        "diagnostics": external_diagnostics(data, training, calibration, diagnostics, learned),
        "fresh_independent_farms": 0,
        "promoted": False,
        "input_hashes": {
            "observations": digest(args.observations),
            "soil": digest(args.soil),
            "iowa": digest(args.iowa),
            "iowa_soil": digest(args.iowa_soil),
            "roles_artifact": digest(ARTIFACT),
            "terrain": terrain,
            "stations": {row.station.id: row.digests for row in rows},
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("observations", "soil", "iowa", "iowa-soil", "cache", "external-cache", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--fetch-missing", action="store_true")
    parser.add_argument(
        "--allow-missing-weather",
        action="store_true",
        help="Use cached ERA5 weather only and impute unavailable stations inside each fold.",
    )
    parser.add_argument("--frozen-report", type=Path)
    for name in ("fresh-observations", "fresh-manifest", "fresh-soil"):
        parser.add_argument(f"--{name}", type=Path)
    args = parser.parse_args()
    fresh_args = (args.frozen_report, args.fresh_observations, args.fresh_manifest, args.fresh_soil)
    if any(fresh_args) and not all(fresh_args):
        parser.error("A fresh diagnostic needs the frozen report, observations, manifest and soil")
    with threadpool_limits(limits=1):
        result = fresh_diagnostic(args) if args.frozen_report else run(args)
    write_atomically(
        args.output, json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )


if __name__ == "__main__":
    main()
