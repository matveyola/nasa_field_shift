"""Compare lightweight maize rotation estimators with grouped and external validation."""

import argparse
import csv
import json
from collections import defaultdict
from collections.abc import Iterator
from hashlib import sha256
from io import StringIO
from math import isfinite
from pathlib import Path
from statistics import fmean, median
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen

import numpy as np
from sklearn.linear_model import Ridge

from field_shift.modules.rotations.yield_model import SOURCE_URL, effect_features
from field_shift_ml.train_moisture import evaluate_predictions, write_atomically
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

BASELINE = "predecessor_mean"
CANDIDATES = (
    BASELINE,
    "predecessor_median",
    "pooled_mean",
    "partially_pooled_mean",
    "ridge_blend_25",
    "ridge_blend_50",
    "ridge_v1",
)
MORROW_URL = "https://sandical.r-universe.dev/morrowplots/data/morrowplots/csv"
MORROW_SHA256 = "9cafffd53c08fbc0bcca12baad1e7abc0d3870317e53c7e98e917cbc18fae2f8"
MORROW_DOI = "https://doi.org/10.13012/B2IDB-7865141_V2"
MORROW_COORDINATES = (40.10445, -88.22582)
BUSHELS_PER_ACRE_TO_T_HA = 56 * 0.45359237 / 0.40468564224 / 1000
ZHOU_URL = (
    "https://zenodo.org/records/13901372/files/"
    "Corn_ExperimentalCalibrationDataWithSoil.csv?download=1"
)
ZHOU_SHA256 = "9df07c56ec6d80786bb9be2df28d9b2ce90d8c9bce81b21a84e99d4f9b4a373f"
ZHOU_DOI = "https://doi.org/10.5281/zenodo.13901372"
EXTRAPOLATION_SITES = {"NE": "SN", "SD": "LN"}


def source_rows(path: Path, url: str, digest: str) -> list[dict[str, str]]:
    cached = path.is_file()
    if cached:
        data = path.read_bytes()
    else:
        request = Request(
            url, headers={"User-Agent": "FieldShift-Offline-Research/1.0", "Accept": "text/csv"}
        )
        with urlopen(request, timeout=30) as response:
            data = response.read(400_001)
    if sha256(data).hexdigest() != digest:
        raise ValueError(f"The data at {path} do not match the pinned research export.")
    if not cached:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="wb", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return list(csv.DictReader(StringIO(data.decode("utf-8"))))


def morrow_contrasts(rows: list[dict[str, str]]) -> list[Sample]:
    by_plot_year = {(row["plot"], int(row["year"])): row for row in rows}
    if len(by_plot_year) != len(rows):
        raise ValueError("Morrow Plots data contain duplicate subplot-year records.")
    contrasts = defaultdict(list)
    for (plot, year), rotated in by_plot_year.items():
        if rotated["plot_num"] != "4" or rotated["phase"] != "5" or rotated["crop"] != "C":
            continue
        previous = by_plot_year.get((plot, year - 1), {})
        control = by_plot_year.get(("3" + plot[1:], year), {})
        if (
            previous.get("crop") != "S"
            or control.get("crop") != "CC"
            or rotated["treatment"] != "LNPK"
            or control["treatment"] != "LNPK"
            or not rotated["nit"]
            or rotated["nit"] != control["nit"]
            or not rotated["yield_bush"]
            or not control["yield_bush"]
        ):
            continue
        rotated_yield = float(rotated["yield_bush"]) * BUSHELS_PER_ACRE_TO_T_HA
        control_yield = float(control["yield_bush"]) * BUSHELS_PER_ACRE_TO_T_HA
        if 0 <= rotated_yield <= 25 and 0 <= control_yield <= 25:
            contrasts[year].append(rotated_yield - control_yield)
    return [
        Sample(
            "Morrow Plots", "Morrow Plots", *MORROW_COORDINATES, year, "Soy", fmean(rows), len(rows)
        )
        for year, rows in sorted(contrasts.items())
    ]


def extrapolation_contrasts(rows: list[dict[str, str]]) -> list[Sample]:
    contrasts = defaultdict(list)
    for row in rows:
        site = row["site"]
        if site not in EXTRAPOLATION_SITES or row["nitrogen"] != EXTRAPOLATION_SITES[site]:
            continue
        effect = float(row["ECRB_Exp"])
        if not isfinite(effect) or abs(effect) > 25:
            raise ValueError("The replication data contain an invalid rotation yield effect.")
        key = (site, int(row["year"]), float(row["latitude"]), float(row["longitude"]))
        contrasts[key].append(effect)
    return [
        Sample(site, site, lat, lon, year, "Soy", fmean(values), len(values))
        for (site, year, lat, lon), values in sorted(contrasts.items())
    ]


def farm_folds(samples: list[Sample]) -> Iterator[tuple[list[Sample], list[Sample]]]:
    for farm in sorted({row.farm for row in samples}):
        yield (
            [row for row in samples if row.farm != farm],
            [row for row in samples if row.farm == farm],
        )


def by_farm(samples: list[Sample]) -> dict[str, list[Sample]]:
    groups = defaultdict(list)
    for row in samples:
        groups[row.farm].append(row)
    return groups


def features(samples: list[Sample]) -> list[tuple[float, ...]]:
    return [effect_features(row.latitude, row.longitude, row.preceding_crop) for row in samples]


def predictions(fit: list[Sample], evaluation: list[Sample]) -> dict[str, np.ndarray]:
    farms = by_farm(fit)
    overall = fmean(fmean(row.delta_t_ha for row in rows) for rows in farms.values())
    means, medians, pooled = {}, {}, {}
    for previous in PREDECESSORS:
        crop_farms = by_farm([row for row in fit if row.preceding_crop == previous])
        centers = [fmean(row.delta_t_ha for row in rows) for rows in crop_farms.values()]
        means[previous] = fmean(centers) if centers else overall
        medians[previous] = median(centers) if centers else overall
        pooled[previous] = fmean([overall, *centers])
    reference = np.array([means[row.preceding_crop] for row in evaluation])
    ridge = Ridge(alpha=6).fit(
        features(fit),
        [row.delta_t_ha for row in fit],
        sample_weight=farm_weights(fit) * len(fit) / len(farms),
    )
    observed_crops = {row.preceding_crop for row in fit}
    ridge_values = np.where(
        [row.preceding_crop in observed_crops for row in evaluation],
        ridge.predict(features(evaluation)),
        overall,
    )
    return {
        BASELINE: reference,
        "predecessor_median": np.array([medians[row.preceding_crop] for row in evaluation]),
        "pooled_mean": np.full(len(evaluation), overall),
        "partially_pooled_mean": np.array([pooled[row.preceding_crop] for row in evaluation]),
        "ridge_blend_25": reference + 0.25 * (ridge_values - reference),
        "ridge_blend_50": reference + 0.50 * (ridge_values - reference),
        "ridge_v1": ridge_values,
    }


def score(samples: list[Sample], predicted: np.ndarray) -> dict[str, float]:
    return evaluate_predictions(
        predicted, np.array([row.delta_t_ha for row in samples]), farm_weights(samples)
    )


def select(samples: list[Sample]) -> tuple[str, dict[str, dict[str, float]]]:
    evaluation: list[Sample] = []
    predictions_by_candidate = {name: [] for name in CANDIDATES}
    for fit, held_out in farm_folds(samples):
        evaluation.extend(held_out)
        for name, values in predictions(fit, held_out).items():
            predictions_by_candidate[name].extend(values)
    metrics = {
        name: score(evaluation, np.asarray(values))
        for name, values in predictions_by_candidate.items()
    }
    objectives = {
        name: fmean(
            values[metric] / metrics[BASELINE][metric]
            if metrics[BASELINE][metric]
            else values[metric]
            for metric in ("mae", "rmse")
        )
        for name, values in metrics.items()
    }
    cutoff = min(objectives.values()) * 1.01
    return next(name for name in CANDIDATES if objectives[name] <= cutoff), metrics


def nested_cv(samples: list[Sample]) -> dict:
    evaluation: list[Sample] = []
    selected_values: list[float] = []
    baseline_values: list[float] = []
    selections = {}
    for fit, held_out in farm_folds(samples):
        selected, _ = select(fit)
        values = predictions(fit, held_out)
        evaluation.extend(held_out)
        selected_values.extend(values[selected])
        baseline_values.extend(values[BASELINE])
        selections[held_out[0].farm] = selected
    return {
        "candidate": score(evaluation, np.asarray(selected_values)),
        "baseline": score(evaluation, np.asarray(baseline_values)),
        "selection_by_held_out_farm": selections,
    }


def diagnostic(fit: list[Sample], evaluation: list[Sample], selected: str, interval: float) -> dict:
    values = predictions(fit, evaluation)
    targets = np.array([row.delta_t_ha for row in evaluation])
    return {
        "candidate": score(evaluation, values[selected]),
        "baseline": score(evaluation, values[BASELINE]),
        "ridge_v1": score(evaluation, values["ridge_v1"]),
        "farms": sorted(by_farm(evaluation)),
        "farm_years": len(evaluation),
        "interval_coverage": float(
            np.average(
                abs(targets - values[selected]) <= interval, weights=farm_weights(evaluation)
            )
        ),
    }


def evaluate(samples: list[Sample], external: list[Sample], extrapolation: list[Sample]) -> dict:
    fit = [row for row in samples if row.farm not in CALIBRATION_FARMS | TEST_FARMS]
    calibration = [row for row in samples if row.farm in CALIBRATION_FARMS]
    test = [row for row in samples if row.farm in TEST_FARMS]
    selected, selection_scores = select(fit)
    errors = abs(
        np.array([row.delta_t_ha for row in calibration]) - predictions(fit, calibration)[selected]
    )
    order = np.argsort(errors)
    weights = farm_weights(calibration)
    index = np.searchsorted(np.cumsum(weights[order]), 0.9 * weights.sum())
    interval = float(errors[order[index]])
    external_distance = check_independent(samples, external)
    extrapolation_distance = check_independent(samples + external, extrapolation)
    external_result = diagnostic(fit, external, selected, interval)
    external_by_predecessor = {
        previous: [row for row in external if row.preceding_crop == previous]
        for previous in PREDECESSORS
    }
    return {
        "status": "research_only",
        "release_gate": {
            "fresh_farm_coverage": len(external_result["farms"]) >= 3,
            "fresh_predecessor_coverage": all(
                len(by_farm(rows)) >= 2 and len(rows) >= 6
                for rows in external_by_predecessor.values()
            ),
            "fresh_mae_improvement": (
                external_result["candidate"]["mae"] <= 0.95 * external_result["baseline"]["mae"]
            ),
            "fresh_rmse_improvement": (
                external_result["candidate"]["rmse"] <= 0.95 * external_result["baseline"]["rmse"]
            ),
            "fresh_interval_coverage": external_result["interval_coverage"] >= 0.7,
        },
        "data_sources": {
            "usda": {"url": SOURCE_URL, "sha256": WORKBOOK_SHA256},
            "morrow_plots": {"url": MORROW_DOI, "export": MORROW_URL, "sha256": MORROW_SHA256},
            "zhou_et_al": {"url": ZHOU_DOI, "export": ZHOU_URL, "sha256": ZHOU_SHA256},
        },
        "fitting_farms": sorted(by_farm(fit)),
        "calibration_farms": sorted(by_farm(calibration)),
        "selected": selected,
        "selection_scores": selection_scores,
        "nested_development_cv": nested_cv(fit),
        "interval_half_width_t_ha": interval,
        "usda_v1_test_regression_diagnostic": diagnostic(fit, test, selected, interval),
        "usda_v1_test_by_predecessor": {
            previous: diagnostic(
                fit, [row for row in test if row.preceding_crop == previous], selected, interval
            )
            for previous in PREDECESSORS
        },
        "morrow_plots_external_diagnostic": {
            **external_result,
            "matched_subplot_pairs": sum(row.management_pairs for row in external),
            "years": [row.year for row in external],
            "predecessors": sorted({row.preceding_crop for row in external}),
            "min_distance_from_usda_km": external_distance,
        },
        "zhou_et_al_extrapolation_diagnostic": {
            **diagnostic(fit, extrapolation, selected, interval),
            "min_distance_from_usda_or_morrow_km": extrapolation_distance,
            "by_farm": {
                farm: {
                    **diagnostic(fit, rows, selected, interval),
                    "nitrogen": EXTRAPOLATION_SITES[farm],
                }
                for farm, rows in sorted(by_farm(extrapolation).items())
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--morrow", type=Path, required=True)
    parser.add_argument("--zhou", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(
        matched_yields(args.workbook),
        morrow_contrasts(source_rows(args.morrow, MORROW_URL, MORROW_SHA256)),
        extrapolation_contrasts(source_rows(args.zhou, ZHOU_URL, ZHOU_SHA256)),
    )
    write_atomically(args.output, json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(f"Selected {report['selected']}. Report written to {args.output}.")


if __name__ == "__main__":
    main()
