"""Search root-zone feature sets and models with nested buffered station validation."""

import argparse
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from hashlib import sha256
from itertools import combinations, product
from pathlib import Path

import numpy as np
from sklearn.ensemble import (
    ExtraTreesRegressor,
    HistGradientBoostingRegressor,
    RandomForestRegressor,
)
from sklearn.kernel_ridge import KernelRidge
from sklearn.linear_model import ElasticNet, HuberRegressor, Ridge
from threadpoolctl import threadpool_limits

from field_shift.modules.crops.fao56 import crop_water_parameters
from field_shift.modules.simulation.root_zone_model import CAPACITY_MM
from field_shift_ml.fetch_root_zone_observations import PROTOCOL_PATH, RootZoneSite
from field_shift_ml.research_root_zone import WILTING_MM, distance, measured_total, raw_total
from field_shift_ml.root_zone_features import FEATURES, feature_matrices
from field_shift_ml.train_moisture import write_atomically
from field_shift_ml.train_root_zone import Sample, assemble, load_targets, weights

RANDOM_STATE = 591
STRENGTHS = (0.25, 0.5, 1.0)
RADII_KM = (0, 150, 300, 500)
FAMILY_PRIORITY = (
    "bias",
    "ridge",
    "ridge_absolute",
    "elastic_net",
    "huber",
    "kernel_ridge",
    "extra_trees",
    "random_forest",
    "hist_gradient_boosting",
)


@dataclass(frozen=True)
class Fit:
    family: str
    groups: tuple[str, ...]
    parameters: tuple[float, ...] = ()

    @property
    def width(self) -> int:
        return sum(len(FEATURES[group]) for group in self.groups)


@dataclass(frozen=True)
class Candidate:
    fit: Fit
    strength: float
    radius_km: int


@dataclass(frozen=True)
class Fold:
    train: tuple[int, ...]
    test: tuple[int, ...]


def fit_grid(include_soil: bool, include_history: bool) -> tuple[Fit, ...]:
    optional_sources = {"soil": include_soil, "history": include_history}
    optional = tuple(
        name for name in FEATURES if name != "core" and optional_sources.get(name, True)
    )
    bundles = [
        ("core", *groups)
        for count in range(len(optional) + 1)
        for groups in combinations(optional, count)
    ]
    all_groups = bundles[-1]
    anchors = [
        bundle for bundle in bundles if len(bundle) in (1, 2, len(optional), len(all_groups))
    ]
    families = [
        Fit("bias", ()),
        *(Fit("ridge", groups, (100,)) for groups in bundles),
        *(Fit("ridge", groups, (alpha,)) for groups, alpha in product(anchors, (1, 10, 300, 1000))),
    ]
    nonlinear_bundles = [
        ("core",),
        ("core", "layers", "weather", "trajectory", "physics"),
        tuple(group for group in all_groups if group != "geography"),
        all_groups,
    ]
    parameters = {
        "ridge_absolute": [(10,), (100,), (300,)],
        "elastic_net": list(product((0.05, 0.5, 3), (0.2, 0.8))),
        "huber": list(product((1.35, 2), (0.1, 10))),
        "kernel_ridge": list(product((1, 20), (0.1, 1))),
        "extra_trees": [(2, 12), (5, 12), (5, 30)],
        "random_forest": [(2, 12), (5, 12), (5, 30)],
        "hist_gradient_boosting": [(4, 0.05, 1), (8, 0.05, 10), (8, 0.10, 1)],
    }
    for family, settings in parameters.items():
        families.extend(
            Fit(family, groups, setting) for groups, setting in product(nonlinear_bundles, settings)
        )
    return tuple(dict.fromkeys(families))


def model(fit: Fit):
    family, args = fit.family, fit.parameters
    if family in ("ridge", "ridge_absolute"):
        return Ridge(alpha=args[0])
    if family == "elastic_net":
        return ElasticNet(alpha=args[0], l1_ratio=args[1], max_iter=4000, tol=1e-3)
    if family == "huber":
        return HuberRegressor(epsilon=args[0], alpha=args[1], max_iter=600)
    if family == "kernel_ridge":
        return KernelRidge(alpha=args[0], gamma=args[1] / fit.width, kernel="rbf")
    if family in ("extra_trees", "random_forest"):
        estimator = ExtraTreesRegressor if family == "extra_trees" else RandomForestRegressor
        return estimator(
            n_estimators=32,
            max_depth=args[0],
            min_samples_leaf=args[1],
            max_features=0.8,
            n_jobs=1,
            random_state=RANDOM_STATE,
        )
    if family == "hist_gradient_boosting":
        return HistGradientBoostingRegressor(
            max_iter=50,
            max_leaf_nodes=args[0],
            learning_rate=args[1],
            l2_regularization=args[2],
            min_samples_leaf=16,
            early_stopping=False,
            random_state=RANDOM_STATE,
        )
    raise ValueError(f"Unknown search model family {family}")


def buffered_folds(samples: list[Sample], indices: tuple[int, ...], buffer_km: int) -> list[Fold]:
    sites = {samples[index].site for index in indices}
    folds = []
    for site in sorted(sites, key=lambda row: row.station_id):
        allowed = {
            other
            for other in sites
            if other.block != site.block and distance(site, other) >= buffer_km
        }
        if len(allowed) < 3:
            raise ValueError(f"Too few independent training stations for {site.station_id}")
        folds.append(
            Fold(
                tuple(index for index in indices if samples[index].site in allowed),
                tuple(index for index in indices if samples[index].site == site),
            )
        )
    return folds


def imputed_scaled(train, test, weight) -> tuple[np.ndarray, np.ndarray]:
    medians = np.array(
        [
            np.median(column[np.isfinite(column)]) if np.isfinite(column).any() else 0.0
            for column in train.T
        ]
    )
    train = np.where(np.isfinite(train), train, medians)
    test = np.where(np.isfinite(test), test, medians)
    center = np.average(train, weights=weight, axis=0)
    train = train - center
    test = test - center
    scale = np.sqrt(np.average(train**2, weights=weight, axis=0))
    constant = scale < 1e-12
    scale[constant] = 1
    train[:, constant] = 0
    test[:, constant] = 0
    return train / scale, test / scale


def metrics(predicted: np.ndarray, actual: np.ndarray, weight: np.ndarray) -> np.ndarray:
    errors = predicted - actual
    return np.column_stack(
        (
            np.average(np.abs(errors), weights=weight, axis=1),
            np.sqrt(np.average(errors**2, weights=weight, axis=1)),
            np.average(errors, weights=weight, axis=1),
        )
    )


def metric_record(values) -> dict:
    return dict(zip(("mae", "rmse", "bias"), map(float, values), strict=True))


def ifs_join_sha256(samples: list[Sample]) -> str:
    digest = sha256()
    pairs = defaultdict(list)
    for row in samples:
        pairs[row.site.station_id, row.run.initialized_on].append(row)
    for key, rows in sorted(pairs.items()):
        ordered = sorted(rows, key=lambda row: row.day.lead_days)
        record = (key, asdict(ordered[0].run), [row.day.lead_days for row in ordered])
        encoded = json.dumps(record, sort_keys=True, default=lambda value: value.isoformat())
        digest.update(encoded.encode())
        digest.update(b"\n")
    return digest.hexdigest()


class Search:
    def __init__(
        self,
        samples: list[Sample],
        targets: dict,
        soil: dict,
        history: dict,
        fit_indices: tuple[int, ...],
        *,
        stress_gate: bool = False,
    ):
        self.samples = samples
        self.total = measured_total(samples, targets)
        self.raw = raw_total(samples)
        self.reserve = np.array([row.actual for row in samples])
        self.physical = np.array([row.baseline for row in samples])
        self.site = np.array([row.site.station_id for row in samples])
        self.lead = np.array([row.day.lead_days for row in samples])
        self.stress_gate = stress_gate
        crop = crop_water_parameters("maize")
        assert crop is not None
        self.threshold = (1 - crop.depletion_fraction) * CAPACITY_MM
        matrices = feature_matrices(samples, soil, history)
        fit_rows = np.array(fit_indices)
        self.optional_support = {
            group: len(set(self.site[fit_rows][np.isfinite(matrices[group][fit_rows]).any(axis=1)]))
            for group in ("soil", "history")
        }
        enabled = {"soil": bool(soil), "history": bool(history)}
        self.fits = fit_grid(enabled["soil"], enabled["history"])
        if stress_gate:
            self.fits = tuple(fit for fit in self.fits if fit.family in ("bias", "ridge"))
        feature_groups = tuple(group for group in FEATURES if enabled.get(group, True))
        self.matrix = np.column_stack([matrices[group] for group in feature_groups])
        columns = np.cumsum([0, *(len(FEATURES[group]) for group in feature_groups)])
        group_columns = {
            group: np.arange(columns[index], columns[index + 1])
            for index, group in enumerate(feature_groups)
        }
        self.group_columns = {
            groups: np.concatenate([group_columns[group] for group in groups])
            for groups in {fit.groups for fit in self.fits if fit.groups}
        }
        self.candidates = tuple(
            Candidate(fit, strength, radius)
            for fit, strength, radius in product(self.fits, STRENGTHS, RADII_KM)
        )
        self.fit_index = np.repeat(np.arange(len(self.fits)), len(STRENGTHS) * len(RADII_KM))
        self.strength = np.array([candidate.strength for candidate in self.candidates])
        self.radius = np.array([candidate.radius_km for candidate in self.candidates])
        self.validation_by_training: dict[tuple[int, ...], set[int]] = defaultdict(set)
        self.prediction_cache: dict[tuple[int, ...], tuple[np.ndarray, np.ndarray]] = {}

    def weight(self, indices) -> np.ndarray:
        return weights([self.samples[index] for index in indices])

    def corrections(self, fold: Fold) -> np.ndarray:
        registered = self.validation_by_training[fold.train]
        missing = set(fold.test) - registered
        cached = self.prediction_cache.get(fold.train)
        if cached is not None and not missing:
            indices, predictions = cached
            return predictions[:, np.searchsorted(indices, fold.test)]
        registered.update(missing)
        train = np.array(fold.train)
        test = np.array(sorted(registered))
        weight = self.weight(train)
        residual = self.total[train] - self.raw[train]
        bias = np.average(residual, weights=weight)
        train_x, test_x = imputed_scaled(self.matrix[train], self.matrix[test], weight)
        scaled = {
            groups: (train_x[:, columns], test_x[:, columns])
            for groups, columns in self.group_columns.items()
        }
        output = []
        for fit in self.fits:
            if fit.family == "bias":
                output.append(np.full(len(test), bias))
                continue
            predictors, inference = scaled[fit.groups]
            absolute = fit.family == "ridge_absolute"
            labels = self.total[train] if absolute else residual
            prediction = model(fit).fit(predictors, labels, sample_weight=weight).predict(inference)
            output.append(prediction - self.raw[test] if absolute else prediction)
        predictions = np.stack(output)
        self.prediction_cache[fold.train] = test, predictions
        return predictions[:, np.searchsorted(test, fold.test)]

    def storage(self, fold: Fold) -> np.ndarray:
        train_sites = {self.samples[index].site for index in fold.train}
        test_sites = {self.samples[index].site for index in fold.test}
        nearest = {site: min(distance(site, other) for other in train_sites) for site in test_sites}
        distances = np.array([nearest[self.samples[index].site] for index in fold.test])
        taper = np.maximum(0, 1 - distances / np.maximum(1, self.radius[:, None]))
        taper[self.radius == 0] = 1
        adjustment = self.corrections(fold)[self.fit_index] * self.strength[:, None] * taper
        raw = self.raw[np.array(fold.test)]
        return np.vstack((raw, np.clip(raw + adjustment, 0, 1000)))

    def pooled(self, folds: list[Fold]) -> tuple[np.ndarray, np.ndarray]:
        return np.concatenate([fold.test for fold in folds]), np.concatenate(
            [self.storage(fold) for fold in folds], axis=1
        )

    def rank(self, indices: np.ndarray, storage: np.ndarray) -> dict:
        reserve = np.clip(storage - WILTING_MM, 0, CAPACITY_MM)
        weight = self.weight(indices)
        reserve_score = metrics(reserve, self.reserve[indices], weight)
        total_score = metrics(storage, self.total[indices], weight)
        all_scores = np.column_stack((reserve_score[:, :2], total_score[:, :2]))
        objective = np.mean(all_scores / np.maximum(all_scores[0], 1e-9), axis=1)
        eligible = np.all(reserve_score[:, :2] <= 0.95 * reserve_score[0, :2], axis=1)
        eligible &= np.all(total_score[:, :2] <= total_score[0, :2], axis=1)
        for group in (self.site[indices], self.lead[indices]):
            for value in np.unique(group):
                keep = group == value
                scores = metrics(
                    reserve[:, keep], self.reserve[indices[keep]], self.weight(indices[keep])
                )
                eligible &= scores[:, 1] <= 1.10 * scores[0, 1] + 1e-9
        if self.stress_gate:
            observed_stress = self.reserve[indices] < self.threshold
            physical_stress = self.physical[indices] < self.threshold
            predicted_stress = reserve < self.threshold
            misses = np.sum(observed_stress & ~predicted_stress, axis=1)
            alarms = np.sum(~observed_stress & predicted_stress, axis=1)
            eligible &= misses <= np.sum(observed_stress & ~physical_stress)
            eligible &= alarms <= np.sum(~observed_stress & physical_stress)
        eligible[0] = False
        return {
            "reserve": reserve_score,
            "storage": total_score,
            "objective": objective,
            "eligible": eligible,
        }

    def choose(self, ranking: dict, options: np.ndarray | None = None) -> int:
        if options is None:
            options = np.flatnonzero(ranking["eligible"])
        if len(options) == 0:
            return 0
        objective = ranking["objective"]
        close = options[objective[options] <= 1.01 * np.min(objective[options])]

        def simplicity(index):
            if index == 0:
                return False, -1, 0, 0, objective[index], index
            fit = self.candidates[index - 1].fit
            return (
                ("soil" in fit.groups) + ("history" in fit.groups),
                FAMILY_PRIORITY.index(fit.family),
                fit.width,
                self.candidates[index - 1].radius_km == 0,
                objective[index],
                index,
            )

        return int(min(close, key=simplicity))

    def config(self, index: int) -> dict:
        return asdict(self.candidates[index - 1]) if index else {"fit": {"family": "direct_ifs"}}

    def measure(self, indices, storage, *, compare_by_group: bool = False) -> dict:
        baseline = self.physical[indices]
        reserve = np.vstack((baseline, np.clip(storage - WILTING_MM, 0, CAPACITY_MM)))
        weight = self.weight(indices)
        all_reserve = metrics(reserve, self.reserve[indices], weight)
        all_total = metrics(storage, self.total[indices], weight)
        names = ("fao56", "raw_ifs", "previous_compact_ridge", "new_candidate")
        stress = self.reserve[indices] < self.threshold
        predicts_stress = reserve < self.threshold
        result = {
            name: {
                "reserve_mm": metric_record(all_reserve[index]),
                "total_storage_mm": metric_record(all_total[index - 1]) if index else None,
                "reference_stress": {
                    "missed": int(np.sum(stress & ~predicts_stress[index])),
                    "false_alarm": int(np.sum(~stress & predicts_stress[index])),
                },
            }
            for index, name in enumerate(names)
        }
        if compare_by_group:
            for name, group in (("site", self.site[indices]), ("lead", self.lead[indices])):
                result[f"by_{name}"] = {
                    str(value): self.measure(indices[group == value], storage[:, group == value])
                    for value in np.unique(group)
                }
        return {"pairs": len(indices), "observed_stress_pairs": int(np.sum(stress)), **result}


def _config_index(search: Search, candidate: Candidate) -> int:
    return search.candidates.index(candidate) + 1


def search_development(
    search: Search, indices: tuple[int, ...], buffer_km: int
) -> tuple[int, dict]:
    folds = buffered_folds(search.samples, indices, buffer_km)
    inner_by_outer = {fold: buffered_folds(search.samples, fold.train, buffer_km) for fold in folds}
    for outer, inner in inner_by_outer.items():
        for fold in (outer, *inner):
            search.validation_by_training[fold.train].update(fold.test)
    prior_index = _config_index(search, Candidate(Fit("ridge", ("core",), (100,)), 0.5, 300))
    nested = []
    selection = []
    for fold in folds:
        inner = inner_by_outer[fold]
        validation, predictions = search.pooled(inner)
        selected = search.choose(search.rank(validation, predictions))
        nested.append(search.storage(fold)[[0, prior_index, selected]])
        selection.append(
            {
                "site": search.samples[fold.test[0]].site.station_id,
                "inner_sites": len(inner),
                "selected": search.config(selected),
            }
        )
        print(f"Water outer validation {len(selection)}/{len(folds)}", flush=True)
    validation, predictions = search.pooled(folds)
    ranking = search.rank(validation, predictions)
    selected = search.choose(ranking)
    candidate_indices = np.arange(1, len(search.candidates) + 1)
    family_summary = {}
    for family in FAMILY_PRIORITY:
        options = np.array(
            [
                index
                for index in candidate_indices
                if search.candidates[index - 1].fit.family == family
            ]
        )
        if len(options) == 0:
            continue
        best = search.choose(ranking, options)
        family_summary[family] = {
            "configurations": len(options),
            "passed": int(np.count_nonzero(ranking["eligible"][options])),
            "best": search.config(best),
            "reserve_mm": metric_record(ranking["reserve"][best]),
            "total_storage_mm": metric_record(ranking["storage"][best]),
        }
    ablations = {}
    for group in FEATURES:
        has_group = np.array([group in candidate.fit.groups for candidate in search.candidates])
        if not has_group.any():
            continue
        matches = {"with": candidate_indices[has_group], "without": candidate_indices[~has_group]}
        ablations[group] = {
            direction: {
                "best": search.config(index),
                "reserve_mm": metric_record(ranking["reserve"][index]),
                "total_storage_mm": metric_record(ranking["storage"][index]),
            }
            for direction, options in matches.items()
            for index in (search.choose(ranking, options),)
        }
    return selected, {
        "configuration_count": len(search.candidates),
        "fit_count_per_fold": len(search.fits),
        "unique_fit_contexts": len(search.prediction_cache),
        "gated_configuration_count": int(np.count_nonzero(ranking["eligible"])),
        "outer_selection": selection,
        "nested": search.measure(validation, np.concatenate(nested, axis=1), compare_by_group=True),
        "development_selection": {
            "candidate": search.config(selected),
            "evaluation": search.measure(
                validation, predictions[[0, prior_index, selected]], compare_by_group=True
            ),
            "families": family_summary,
            "feature_group_comparison": ablations,
        },
    }


def run(args) -> dict:
    protocol = json.loads(PROTOCOL_PATH.read_bytes())
    manifest, targets = load_targets(args.observations)
    source = args.soil_snapshot.read_bytes() if args.soil_snapshot else None
    soil_entries = json.loads(source)["sites"] if source else {}
    soils = {station: entry["features"] for station, entry in soil_entries.items()}
    history_source = args.history_snapshot.read_bytes() if args.history_snapshot else None
    history_entries = json.loads(history_source)["sites"] if history_source else {}
    history = {station: entry["days"] for station, entry in history_entries.items()}
    sites = [
        RootZoneSite(**{key: value for key, value in item.items() if key != "days"})
        for item in manifest["sites"]
        if item["days"] and item["role"] != "test"
    ]
    rows = assemble(
        sites, protocol["fit_and_calibration"], targets, args.weather_cache, protocol["model_cycle"]
    )
    fit = tuple(index for index, row in enumerate(rows) if row.site.role == "fit")
    calibration = tuple(index for index, row in enumerate(rows) if row.site.role == "calibration")
    with threadpool_limits(limits=1):
        search = Search(rows, targets, soils, history, fit, stress_gate=args.stress_gate)
        candidate, development = search_development(search, fit, protocol["spatial_buffer_km"])
        prior = _config_index(search, Candidate(Fit("ridge", ("core",), (100,)), 0.5, 300))
        calibration_output = search.storage(Fold(fit, calibration))[[0, prior, candidate]]
        diagnostic = search.measure(
            np.array(calibration), calibration_output, compare_by_group=True
        )
    result = {
        "schema_version": 1,
        "model_cycle": protocol["model_cycle"],
        "observations_sha256": manifest["observations_sha256"],
        "site_protocol_sha256": manifest["site_protocol_sha256"],
        "ifs_join_sha256": ifs_join_sha256(rows),
        "soil_snapshot_sha256": sha256(source).hexdigest() if source else None,
        "soil_sources": {
            station: entry["source_sha256"] for station, entry in soil_entries.items()
        },
        "history_snapshot_sha256": sha256(history_source).hexdigest() if history_source else None,
        "history_sources": {
            station: entry["source_sha256"] for station, entry in history_entries.items()
        },
        "static_and_history_fit_support": search.optional_support,
        "development": development,
        "prior_calibration_diagnostic": diagnostic,
        "fresh_managed_farm_validation": False,
        "promoted": False,
    }
    if args.stress_gate:
        result["stress_gate"] = {
            "baseline": "fao56",
            "threshold_mm": search.threshold,
            "counts": "missed stress and false alarms each at most FAO-56 in inner validation",
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--weather-cache", type=Path, required=True)
    parser.add_argument("--soil-snapshot", type=Path)
    parser.add_argument("--history-snapshot", type=Path)
    parser.add_argument("--stress-gate", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args)
    write_atomically(
        args.output, json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    print(json.dumps(report["development"]["development_selection"]["candidate"], sort_keys=True))


if __name__ == "__main__":
    main()
