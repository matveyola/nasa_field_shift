"""Search agronomic feature combinations and estimators for maize rotation contrasts."""

import argparse
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from hashlib import sha256
from itertools import combinations, product
from pathlib import Path
from statistics import fmean

import numpy as np
from sklearn.ensemble import ExtraTreesRegressor, GradientBoostingRegressor
from sklearn.kernel_ridge import KernelRidge
from sklearn.linear_model import ElasticNet, HuberRegressor, Ridge
from threadpoolctl import threadpool_limits

from field_shift.modules.geo_ml.ssurgo import SDA_ENDPOINT, SoilProfile
from field_shift.modules.rotations.yield_model import SOURCE_URL, site_distance
from field_shift_ml.fetch_soil_profiles import fetch_snapshot
from field_shift_ml.iterate_rotation_yield import (
    BASELINE,
    MORROW_DOI,
    MORROW_SHA256,
    MORROW_URL,
    ZHOU_DOI,
    ZHOU_SHA256,
    ZHOU_URL,
    by_farm,
    extrapolation_contrasts,
    farm_folds,
    morrow_contrasts,
    predictions,
    score,
    source_rows,
)
from field_shift_ml.rotation_external import KBS_SHA256, KBS_SOURCE, kbs_contrasts, kbs_source_bytes
from field_shift_ml.rotation_features import (
    CLIMATE_YEARS,
    DAYMET_SOURCE,
    FEATURE_NAMES,
    Values,
    aggregate_soil_snapshot,
    climate_snapshot,
    feature_groups,
    location_of,
    median_if_supported,
    soil_values,
)
from field_shift_ml.train_moisture import write_atomically
from field_shift_ml.train_rotation_yield import (
    CALIBRATION_FARMS,
    PREDECESSORS,
    TEST_FARMS,
    WORKBOOK_SHA256,
    Sample,
    check_independent,
    farm_weights,
    matched_yields,
)

GroupSet = tuple[str, ...]
FeatureTable = dict[Sample, dict[str, Values]]
_FAMILY_ORDER = (
    "mean",
    "ridge_v1",
    "ridge",
    "elastic_net",
    "huber",
    "kernel_ridge",
    "extra_trees",
    "gradient_boosting",
)


@dataclass(frozen=True, slots=True)
class Candidate:
    family: str
    groups: GroupSet = ()
    params: tuple[tuple[str, float], ...] = ()
    interactions: bool = False
    taper_km: float | None = None

    @property
    def key(self) -> str:
        parts = [self.family, "+".join(self.groups) or "predecessor"]
        parts.extend(f"{name}={value:g}" for name, value in self.params)
        if self.interactions:
            parts.append("interactions")
        if self.taper_km is not None:
            parts.append(f"taper={self.taper_km:g}km")
        return "/".join(parts)

    @property
    def complexity(self) -> tuple[int, int, bool, bool]:
        return (
            len(self.groups),
            _FAMILY_ORDER.index(self.family),
            self.interactions,
            self.taper_km is not None,
        )


REFERENCE = Candidate("mean")
RUNTIME_RIDGE = Candidate("ridge_v1", ("geo",))


def search_space() -> tuple[Candidate, ...]:
    names = tuple(FEATURE_NAMES)
    subsets = [groups for size in range(len(names) + 1) for groups in combinations(names, size)]
    candidates = [REFERENCE, RUNTIME_RIDGE]
    for groups in subsets:
        candidates.extend(
            Candidate("ridge", groups, (("alpha", alpha),), interactions)
            for alpha, interactions in product((0.3, 1.0, 3.0, 10.0, 30.0, 100.0), (False, True))
            if groups or not interactions
        )
        candidates.extend(
            Candidate("elastic_net", groups, (("alpha", alpha), ("l1_ratio", ratio)))
            for alpha, ratio in product((0.04, 0.12, 0.35), (0.2, 0.8))
        )
        candidates.extend(
            Candidate("huber", groups, (("alpha", alpha), ("epsilon", epsilon)))
            for alpha, epsilon in product((0.3, 3.0), (1.35, 1.8))
        )
        if not groups:
            continue
        candidates.extend(
            Candidate("ridge", groups, (("alpha", alpha),), interactions=True, taper_km=radius)
            for alpha, radius in product((0.3, 3.0, 30.0), (150.0, 250.0, 400.0))
        )
        candidates.extend(
            Candidate("kernel_ridge", groups, (("alpha", alpha), ("gamma", gamma)))
            for alpha, gamma in product((0.25, 2.0), (0.125, 0.5, 2.0))
        )
        candidates.extend(
            Candidate("extra_trees", groups, (("max_depth", depth), ("min_samples_leaf", leaf)))
            for depth, leaf in product((1, 2, 4), (2, 4))
        )
        candidates.extend(
            Candidate("gradient_boosting", groups, (("max_depth", depth), ("learning_rate", rate)))
            for depth, rate in product((1, 2), (0.03, 0.08))
        )
    return tuple(candidates)


def estimator(candidate: Candidate):
    params = dict(candidate.params)
    if candidate.family == "ridge":
        return Ridge(**params)
    if candidate.family == "elastic_net":
        return ElasticNet(max_iter=5000, **params)
    if candidate.family == "huber":
        return HuberRegressor(max_iter=800, **params)
    if candidate.family == "kernel_ridge":
        return KernelRidge(kernel="rbf", **params)
    params["max_depth"] = int(params["max_depth"])
    if candidate.family == "extra_trees":
        params["min_samples_leaf"] = int(params["min_samples_leaf"])
        return ExtraTreesRegressor(n_estimators=24, random_state=19, n_jobs=1, **params)
    if candidate.family == "gradient_boosting":
        return GradientBoostingRegressor(
            n_estimators=32, min_samples_leaf=3, loss="huber", random_state=19, **params
        )
    raise ValueError(f"Unknown research estimator {candidate.family}.")


def raw_row(row: Sample, groups: GroupSet, features: FeatureTable) -> Values:
    return tuple(value for group in groups for value in features[row][group])


def is_supported(row: Sample, groups: GroupSet, features: FeatureTable) -> bool:
    return all(any(value is not None for value in features[row][group]) for group in groups)


def feature_matrices(
    fit: list[Sample],
    evaluation: list[Sample],
    groups: GroupSet,
    features: FeatureTable,
    weights: np.ndarray,
    interactions: bool,
) -> tuple[np.ndarray, np.ndarray]:
    source = [raw_row(row, groups, features) for row in fit]
    medians = [median_if_supported(values) for values in zip(*source, strict=True)]
    keep = [index for index, value in enumerate(medians) if value is not None]

    def values(rows: list[Values]) -> np.ndarray:
        return np.array(
            [
                [row[index] if row[index] is not None else medians[index] for index in keep]
                for row in rows
            ],
            dtype=float,
        )

    def add_crop(rows: list[Sample], matrix: np.ndarray) -> np.ndarray:
        wheat = np.array([row.preceding_crop == "Wheat" for row in rows], dtype=float)
        columns = [wheat, matrix]
        if interactions:
            columns.append(wheat[:, None] * matrix)
            group_names = [name for group in groups for name in FEATURE_NAMES[group]]
            named = {group_names[index]: column for column, index in enumerate(keep)}
            for left, right in (
                ("summer_vpd_kpa", "available_water_0_100_cm_mm"),
                ("summer_dry_spell_days", "no_till_share"),
            ):
                if left in named and right in named:
                    columns.append(matrix[:, named[left]] * matrix[:, named[right]])
        return np.column_stack(columns)

    fitting = values(source)
    held = values([raw_row(row, groups, features) for row in evaluation])
    if keep:
        varied = np.ptp(fitting, axis=0) > 1e-12
        keep = [index for index, usable in zip(keep, varied, strict=True) if usable]
        fitting, held = fitting[:, varied], held[:, varied]
        center = np.average(fitting, axis=0, weights=weights)
        spread = np.sqrt(np.average((fitting - center) ** 2, axis=0, weights=weights))
        fitting, held = (fitting - center) / spread, (held - center) / spread
    return add_crop(fit, fitting), add_crop(evaluation, held)


@dataclass(frozen=True, slots=True)
class FoldOutput:
    rows: list[Sample]
    predictions: dict[Candidate, np.ndarray]
    supported: dict[Candidate, np.ndarray]

    def subset(self, keep: list[bool]) -> "FoldOutput":
        return FoldOutput(
            [row for row, included in zip(self.rows, keep, strict=True) if included],
            {candidate: values[keep] for candidate, values in self.predictions.items()},
            {candidate: values[keep] for candidate, values in self.supported.items()},
        )


def predict_fold(
    fit: list[Sample],
    evaluation: list[Sample],
    features: FeatureTable,
    candidates: Iterable[Candidate],
) -> FoldOutput:
    values = predictions(fit, fit + evaluation)
    base_fit = values[BASELINE][: len(fit)]
    base = values[BASELINE][len(fit) :]
    original = values["ridge_v1"][len(fit) :]
    observed_crops = {row.preceding_crop for row in fit}
    sample_weight = farm_weights(fit) * len(fit) / len(by_farm(fit))
    target = np.array([row.delta_t_ha for row in fit]) - base_fit
    all_predictions, all_support = {}, {}
    matrices = {}
    fitted_predictions = {}
    group_support = {}
    fitting_locations = {
        previous: {(row.latitude, row.longitude) for row in fit if row.preceding_crop == previous}
        for previous in PREDECESSORS
    }
    distances = np.array(
        [
            min(
                (
                    site_distance(row.latitude, row.longitude, *location)
                    for location in fitting_locations[row.preceding_crop]
                ),
                default=np.inf,
            )
            for row in evaluation
        ]
    )
    for candidate in candidates:
        groups = candidate.groups
        if groups not in group_support:
            group_support[groups] = np.array(
                [
                    row.preceding_crop in observed_crops and is_supported(row, groups, features)
                    for row in evaluation
                ]
            )
        support = group_support[groups]
        if candidate == REFERENCE:
            result = base
        elif candidate == RUNTIME_RIDGE:
            result = original
        elif not support.any() or any(
            not any(is_supported(row, (group,), features) for row in fit) for group in groups
        ):
            result = base
            support = np.zeros(len(evaluation), dtype=bool)
        else:
            matrix_key = groups, candidate.interactions
            if matrix_key not in matrices:
                matrices[matrix_key] = feature_matrices(
                    fit, evaluation, groups, features, sample_weight, candidate.interactions
                )
            fit_key = candidate.family, candidate.params, matrix_key
            if fit_key not in fitted_predictions:
                x, held = matrices[matrix_key]
                model = estimator(candidate).fit(x, target, sample_weight=sample_weight)
                fitted_predictions[fit_key] = model.predict(held)
            correction = fitted_predictions[fit_key]
            if candidate.taper_km is not None:
                taper = np.maximum(0, 1 - distances / candidate.taper_km)
                correction = correction * taper
                support = support & (taper > 0)
            result = np.where(support, np.clip(base + correction, -25, 25), base)
        all_predictions[candidate] = result
        all_support[candidate] = support
    return FoldOutput(evaluation, all_predictions, all_support)


def candidate_scores(
    folds: list[FoldOutput], candidates: tuple[Candidate, ...]
) -> dict[Candidate, dict[str, float]]:
    rows = [row for fold in folds for row in fold.rows]
    return {
        candidate: score(rows, np.concatenate([fold.predictions[candidate] for fold in folds]))
        for candidate in candidates
    }


def pick(metrics: dict[Candidate, dict[str, float]]) -> Candidate:
    baseline = metrics[REFERENCE]
    objectives = {
        candidate: fmean(
            values[metric] / baseline[metric] if baseline[metric] else values[metric]
            for metric in ("mae", "rmse")
        )
        for candidate, values in metrics.items()
    }
    cutoff = min(objectives.values()) * 1.01
    return min(
        (candidate for candidate, value in objectives.items() if value <= cutoff),
        key=lambda candidate: (candidate.complexity, objectives[candidate], candidate.key),
    )


@dataclass(frozen=True, slots=True)
class NestedFold:
    fit_scores: dict[Candidate, dict[str, float]]
    evaluation: FoldOutput


def validate(
    development: list[Sample],
    features: FeatureTable,
    candidates: tuple[Candidate, ...],
) -> list[NestedFold]:
    result = []
    inner_cache: dict[tuple[str, ...], FoldOutput] = {}
    for fit, held in farm_folds(development):
        check_independent(fit, held)
        inner = []
        for inner_fit, inner_held in farm_folds(fit):
            check_independent(inner_fit, inner_held)
            fitting_farms = tuple(sorted(by_farm(inner_fit)))
            if fitting_farms not in inner_cache:
                other_farms = [row for row in development if row.farm not in fitting_farms]
                inner_cache[fitting_farms] = predict_fold(
                    inner_fit, other_farms, features, candidates
                )
            cached = inner_cache[fitting_farms]
            inner.append(cached.subset([row.farm == inner_held[0].farm for row in cached.rows]))
        result.append(
            NestedFold(
                candidate_scores(inner, candidates),
                predict_fold(fit, held, features, candidates),
            )
        )
    return result


def summarize_search(
    folds: list[NestedFold],
    development_scores: dict[Candidate, dict[str, float]],
    accept: Callable[[Candidate], bool],
) -> dict:
    selected = pick(
        {candidate: result for candidate, result in development_scores.items() if accept(candidate)}
    )
    outcomes = []
    by_held_farm = {}
    for fold in folds:
        choice = pick(
            {
                candidate: result
                for candidate, result in fold.fit_scores.items()
                if accept(candidate)
            }
        )
        results = fold.evaluation
        predicted = results.predictions[choice]
        outcomes.append(predicted)
        by_held_farm[results.rows[0].farm] = {
            "candidate": choice.key,
            "scores": score(results.rows, predicted),
            "baseline": score(results.rows, results.predictions[REFERENCE]),
            "supported": int(results.supported[choice].sum()),
            "farm_years": len(results.rows),
        }
    rows = [row for fold in folds for row in fold.evaluation.rows]
    return {
        "final_candidate": selected.key,
        "development_selection_score": development_scores[selected],
        "nested_score": score(rows, np.concatenate(outcomes)),
        "by_held_farm": by_held_farm,
    }


def diagnostic(fold: FoldOutput, candidate: Candidate, interval: float) -> dict:
    actual = np.array([row.delta_t_ha for row in fold.rows])
    prediction = fold.predictions[candidate]
    return {
        "farms": sorted(by_farm(fold.rows)),
        "farm_years": len(fold.rows),
        "candidate": score(fold.rows, prediction),
        "baseline": score(fold.rows, fold.predictions[REFERENCE]),
        "geography_ridge_v1": score(fold.rows, fold.predictions[RUNTIME_RIDGE]),
        "supported_predictions": int(fold.supported[candidate].sum()),
        "interval_coverage": float(
            np.average(abs(actual - prediction) <= interval, weights=farm_weights(fold.rows))
        ),
    }


def benchmark(
    trial: list[Sample],
    morrow: list[Sample],
    extrapolation: list[Sample],
    kbs: list[Sample],
    features: FeatureTable,
    candidates: tuple[Candidate, ...] | None = None,
) -> dict:
    candidates = candidates if candidates is not None else search_space()
    development = [row for row in trial if row.farm not in CALIBRATION_FARMS | TEST_FARMS]
    calibration = [row for row in trial if row.farm in CALIBRATION_FARMS]
    original_test = [row for row in trial if row.farm in TEST_FARMS]
    check_independent(development, calibration)
    check_independent(development, original_test)
    check_independent(calibration, original_test)
    check_independent(trial, morrow)
    check_independent(trial + morrow, extrapolation)
    fresh_distance = check_independent(trial + morrow + extrapolation, kbs)
    folds = validate(development, features, candidates)
    scores = candidate_scores([fold.evaluation for fold in folds], candidates)
    best = pick(scores)
    no_plan = pick(
        {
            candidate: values
            for candidate, values in scores.items()
            if "plan" not in candidate.groups
        }
    )
    reference = (REFERENCE, RUNTIME_RIDGE)
    finalists = tuple(dict.fromkeys((*reference, best, no_plan)))
    calibration_fit = predict_fold(development, calibration, features, finalists)
    intervals = {}
    for candidate in (best, no_plan):
        errors = abs(
            np.array([row.delta_t_ha for row in calibration])
            - calibration_fit.predictions[candidate]
        )
        order = np.argsort(errors)
        weights = farm_weights(calibration)
        index = np.searchsorted(np.cumsum(weights[order]), 0.9 * weights.sum())
        intervals[candidate] = float(errors[order[index]])

    def evaluate_held(fold: FoldOutput) -> dict:
        return {
            "full_search": diagnostic(fold, best, intervals[best]),
            "without_plan_features": diagnostic(fold, no_plan, intervals[no_plan]),
        }

    fresh_fold = predict_fold(development, kbs, features, finalists)
    fresh = evaluate_held(fresh_fold)
    full_fresh = fresh["full_search"]
    return {
        "status": "research_only",
        "release_gate": {
            "fresh_sealed_farms": len(by_farm(kbs)) >= 3,
            "fresh_wheat_predecessor_farms": len(
                {row.farm for row in kbs if row.preceding_crop == "Wheat"}
            )
            >= 2,
            "fresh_mae_improvement": full_fresh["candidate"]["mae"]
            <= 0.95 * full_fresh["baseline"]["mae"],
            "fresh_rmse_improvement": full_fresh["candidate"]["rmse"]
            <= 0.95 * full_fresh["baseline"]["rmse"],
            "fresh_interval_coverage": full_fresh["interval_coverage"] >= 0.7,
        },
        "candidates": len(candidates),
        "development_farms": sorted(by_farm(development)),
        "development_farm_years": len(development),
        "development_predecessor_support": {
            previous: sorted({row.farm for row in development if row.preceding_crop == previous})
            for previous in PREDECESSORS
        },
        "calibration_farms": sorted(by_farm(calibration)),
        "baseline_nested_score": scores[REFERENCE],
        "geography_ridge_v1_nested_score": scores[RUNTIME_RIDGE],
        "full_search": summarize_search(folds, scores, lambda _: True),
        "nested_feature_ablation": {
            name: summarize_search(folds, scores, lambda candidate: name not in candidate.groups)
            for name in FEATURE_NAMES
        },
        "nested_single_group": {
            name: summarize_search(folds, scores, lambda candidate: set(candidate.groups) <= {name})
            for name in FEATURE_NAMES
        },
        "nested_family_search": {
            family: summarize_search(
                folds,
                scores,
                lambda candidate: candidate == REFERENCE or candidate.family == family,
            )
            for family in _FAMILY_ORDER[2:]
        },
        "calibrated_interval_half_width_t_ha": {
            candidate.key: interval for candidate, interval in intervals.items()
        },
        "reused_diagnostics": {
            name: evaluate_held(predict_fold(development, rows, features, finalists))
            for name, rows in (
                ("usda_original_holdout", original_test),
                ("morrow_plots", morrow),
                ("nebraska_south_dakota", extrapolation),
            )
        },
        "fresh_kellogg_living_field_lab": {
            **fresh,
            "minimum_distance_from_other_trials_km": fresh_distance,
            "matched_replicate_pairs": sum(row.management_pairs for row in kbs),
            "farm_years_by_predecessor": {
                previous: sum(row.preceding_crop == previous for row in kbs)
                for previous in PREDECESSORS
            },
            "by_predecessor": {
                previous: evaluate_held(
                    fresh_fold.subset([row.preceding_crop == previous for row in kbs])
                )
                for previous in PREDECESSORS
                if any(row.preceding_crop == previous for row in kbs)
            },
        },
        "feature_coverage": {
            name: {
                feature: sum(features[row][name][index] is not None for row in development)
                for index, feature in enumerate(names)
            }
            for name, names in FEATURE_NAMES.items()
        },
        "candidate_development_scores": {
            candidate.key: result for candidate, result in scores.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("workbook", "morrow", "zhou", "kbs", "climate-cache", "soil-cache", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    trial = matched_yields(args.workbook)
    morrow = morrow_contrasts(source_rows(args.morrow, MORROW_URL, MORROW_SHA256))
    extrapolation = extrapolation_contrasts(source_rows(args.zhou, ZHOU_URL, ZHOU_SHA256))
    kbs = kbs_contrasts(kbs_source_bytes(args.kbs))
    if not kbs:
        raise ValueError("The independent KBS audit produced no matching maize contrasts.")
    all_rows = trial + morrow + extrapolation + kbs
    locations = {location_of(row) for row in all_rows}
    climate, climate_digests = climate_snapshot(locations, args.climate_cache)
    soil_locations = {f"{lat:.6f}_{lon:.6f}": (lat, lon) for lat, lon in locations}
    profiles = fetch_snapshot(soil_locations, args.soil_cache, refresh=False)
    aggregates, aggregate_digest = aggregate_soil_snapshot(
        {profile["mukey"] for profile in profiles.values() if profile is not None}, args.soil_cache
    )
    soils = {}
    for key, location in soil_locations.items():
        cached_profile = profiles[key]
        profile = SoilProfile.model_validate(cached_profile) if cached_profile is not None else None
        soils[location] = soil_values(profile, aggregates.get(profile.mukey) if profile else None)
    features = {row: feature_groups(row, climate, soils) for row in all_rows}
    with threadpool_limits(limits=1):
        report = benchmark(trial, morrow, extrapolation, kbs, features)
    report["sources"] = {
        "usda_trial": {"url": SOURCE_URL, "sha256": WORKBOOK_SHA256},
        "morrow": {"url": MORROW_DOI, "sha256": MORROW_SHA256},
        "zhou": {"url": ZHOU_DOI, "sha256": ZHOU_SHA256},
        "kbs": {"url": KBS_SOURCE, "sha256": KBS_SHA256},
        "climate": {
            "url": DAYMET_SOURCE,
            "years": [CLIMATE_YEARS.start, CLIMATE_YEARS.stop - 1],
            "sha256_by_location": climate_digests,
        },
        "soils": {
            "url": SDA_ENDPOINT,
            "snapshot_sha256": sha256(json.dumps(profiles, sort_keys=True).encode()).hexdigest(),
            "locations_available": sum(profile is not None for profile in profiles.values()),
            "aggregate_map_units": len(aggregates),
            "aggregate_sha256": aggregate_digest,
        },
    }
    write_atomically(args.output, json.dumps(report, indent=2, allow_nan=False) + "\n")
    selected = report["full_search"]["final_candidate"]
    print(f"Scored {report['candidates']} variants. Selected {selected}.")


if __name__ == "__main__":
    main()
