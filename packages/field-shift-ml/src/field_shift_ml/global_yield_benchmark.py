"""Fixed retrospective ARDN yield comparison; never a promotion or crop-regret gate.

The protocol is saved before fitting. Source and feature hashes identify membership;
2015 has already been inspected in prior source audits and is not a sealed test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import sklearn
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from field_shift_ml import global_data
from field_shift_ml.global_data import build_panel, issue_date

CANDIDATES = (
    "training_mean",
    "crop_mean",
    "crop_trend",
    "ridge_1",
    "ridge_10",
    "ridge_100",
    "gbdt_2",
    "gbdt_3",
)
PROTOCOL = {
    "version": "ardn-power-preseason-v2",
    "status": "retrospective_predictors_not_operational",
    "promotion_allowed": False,
    "test_sealed": False,
    "test_exposure": "Public source audited previously; diagnostic holdout, not untouched evidence",
    "fit_years": [2011, 2012],
    "select_years": [2013],
    "test_years": [2015],
    "refit_selection_labels": False,
    "excluded_years": [2014],
    "label_availability_lag_days": 365,
    "availability_policy": (
        "All fit and selection labels: observed harvest plus 365 days <= target issue"
    ),
    "lag_basis": "Conservative modeling assumption, not observed publication dates",
    "selection_cohorts": (
        "Separate target crop and issue; fit pooled crops, select target crop only"
    ),
    "selection_role": "Retrospective development split; not a replay of validation-year issuance",
    "amendment": "Before error inspection: avoid winter wheat target-issue label timing leakage",
    "superseded_protocol_sha256": (
        "776fe3a4117048cf8ca7a1390f56364c8b5e7c5292c6b81d1a7b4a7a74bdc446"
    ),
    "issue_schedules": {
        "early": {"maize_soybean": "02-01", "wheat_prior_year": "07-01"},
        "standard": {"maize_soybean": "03-01", "wheat_prior_year": "08-01"},
    },
    "weather_lag_days": 7,
    "climatology_years": [1984, 2009],
    "minimum_coverage": 0.95,
    "features": [
        "crop",
        "latitude",
        "longitude",
        "harvest_year",
        "monthly_climatology",
        "weather_daily_means_last_30_and_90_days_ending_7_days_before_issue",
    ],
    "forbidden_features": [
        "future_weather",
        "actual_sowing_date",
        "harvest_measurements",
        "nitrogen",
        "soil_moisture_assumptions",
        "test_labels",
    ],
    "sowing_usage": "Eligibility and reported lead only; fixed issue date is not learned",
    "spatial_regimes": {
        "temporal": None,
        "unseen_state_WI": "WI",
        "unseen_site_KELLOGG": "KELLOGG",
    },
    "spatial_choice_basis": "Coverage chosen before errors; WI includes all 3 crops",
    "candidates": list(CANDIDATES),
    "gbdt_trees": 100,
    "gbdt_learning_rate": 0.05,
    "seed": 7,
    "selection": "minimum target-crop MAE in eligible 2013 labels; lexical tie break",
    "candidate_families": "Select ridge alpha and GBDT depth separately; baselines retained",
    "fit_weighting": "plot rows; also report physical-site/year/crop macro error",
    "target": "dry t/ha; ARDN is already dry kg/ha, divide by 1000 only",
    "availability_caveat": (
        "Revised POWER; 7-day lag cannot guarantee historical vintage availability"
    ),
    "counterfactual_crop_regret": "unavailable: only the observed planted crop has a label",
    "scope": "US research plots, 2011-2015, 3 crops; no worldwide/Ukraine/SOTA claim",
}


def canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def split_panel(rows: list[dict], regime: str, lead: str, crop: str) -> dict[str, list[dict]]:
    if regime not in PROTOCOL["spatial_regimes"] or lead not in PROTOCOL["issue_schedules"]:
        raise ValueError("Unknown regime or issue schedule")
    held_groups = {
        r["physical_site"]
        for r in rows
        if (regime == "unseen_state_WI" and r["state"] == "WI")
        or (regime == "unseen_site_KELLOGG" and r["physical_site"] == "KELLOGG")
    }
    if crop not in {"maize", "soybean", "wheat"}:
        raise ValueError("Unknown target crop")
    cutoff = issue_date(2015, crop, lead).isoformat()
    result = {"fit": [], "select": [], "test": []}
    seen = set()
    for row in rows:
        if row["lead"] != lead:
            continue
        if row["id"] in seen:
            raise ValueError("Duplicate panel membership within issue schedule")
        seen.add(row["id"])
        held = row["physical_site"] in held_groups
        if (
            row["year"] in PROTOCOL["fit_years"]
            and not held
            and row["label_available_on"] <= cutoff
        ):
            result["fit"].append(row)
        elif (
            row["year"] in PROTOCOL["select_years"]
            and not held
            and row["crop"] == crop
            and row["label_available_on"] <= cutoff
        ):
            result["select"].append(row)
        elif (
            row["year"] in PROTOCOL["test_years"]
            and row["crop"] == crop
            and (regime == "temporal" or held)
        ):
            result["test"].append(row)
    if regime != "temporal":
        train_groups = {r["physical_site"] for r in result["fit"] + result["select"]}
        if train_groups & {r["physical_site"] for r in result["test"]}:
            raise ValueError("Spatial holdout overlaps physical training site")
    return result


def metrics(actual: list[float], predicted: list[float]) -> dict:
    if len(actual) != len(predicted) or not actual:
        raise ValueError("Nonempty aligned predictions required")
    y, p = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    if not np.all(np.isfinite(y)) or not np.all(np.isfinite(p)):
        raise ValueError("Metrics require finite predictions and labels")
    error = p - y
    denominator = float(np.sum((y - y.mean()) ** 2))
    # The mean of identical nonbinary decimals can round away from the value,
    # producing a tiny positive denominator even though target variance is zero.
    constant_target = bool(np.all(y == y[0]))
    return dict(
        n=len(y),
        mae=float(np.mean(np.abs(error))),
        rmse=float(np.sqrt(np.mean(error**2))),
        bias=float(np.mean(error)),
        r2=(
            float(1 - np.sum(error**2) / denominator)
            if len(y) >= 2 and not constant_target and denominator > 0
            else None
        ),
    )


def summarize(rows: list[dict], predictions: list[float]) -> dict:
    if len(rows) != len(predictions):
        raise ValueError("Prediction membership mismatch")
    result = {"overall": metrics([r["yield_t_ha"] for r in rows], predictions)}
    for dimension in ("crop", "state"):
        result[f"by_{dimension}"] = {}
        for key in sorted({r[dimension] for r in rows}):
            pairs = [
                (r["yield_t_ha"], p)
                for r, p in zip(rows, predictions, strict=True)
                if r[dimension] == key
            ]
            result[f"by_{dimension}"][key] = metrics(*map(list, zip(*pairs, strict=True)))
    grouped = defaultdict(list)
    for row, prediction in zip(rows, predictions, strict=True):
        grouped[row["physical_site"], row["year"], row["crop"]].append(
            (row["yield_t_ha"], prediction)
        )
    group_metrics = [metrics(*map(list, zip(*pairs, strict=True))) for pairs in grouped.values()]
    result["site_year_crop_macro"] = dict(
        n_groups=len(grouped),
        mae=float(np.mean([m["mae"] for m in group_metrics])),
        rmse=float(np.sqrt(np.mean([m["rmse"] ** 2 for m in group_metrics]))),
        bias=float(np.mean([m["bias"] for m in group_metrics])),
    )
    result["site_year_crop_means"] = metrics(
        [float(np.mean([y for y, _ in pairs])) for pairs in grouped.values()],
        [float(np.mean([p for _, p in pairs])) for pairs in grouped.values()],
    )
    result["crop_macro_mae"] = float(np.mean([m["mae"] for m in result["by_crop"].values()]))
    result["days_before_sowing"] = dict(
        min=min(r["days_before_sowing"] for r in rows),
        max=max(r["days_before_sowing"] for r in rows),
    )
    return result


def fit_predictor(name: str, training: list[dict]):
    """Fit preprocessing and model only on explicit training membership."""
    if name not in CANDIDATES or not training:
        raise ValueError("Known candidate and nonempty training rows required")
    y = np.asarray([r["yield_t_ha"] for r in training])
    if name == "training_mean":
        value = float(y.mean())
        return lambda rows: [value] * len(rows)
    if name in {"crop_mean", "crop_trend"}:
        models = {}
        for crop in sorted({r["crop"] for r in training}):
            subset = [r for r in training if r["crop"] == crop]
            targets = [r["yield_t_ha"] for r in subset]
            models[crop] = (
                float(np.mean(targets))
                if name == "crop_mean"
                else LinearRegression().fit([[r["year"] - 2011] for r in subset], targets)
            )

        def predict(rows):
            result = []
            for row in rows:
                if row["crop"] not in models:
                    raise ValueError("Crop absent from training")
                model = models[row["crop"]]
                result.append(
                    model
                    if name == "crop_mean"
                    else float(model.predict([[row["year"] - 2011]])[0])
                )
            return result

        return predict
    if name.startswith("ridge_"):
        model = make_pipeline(
            DictVectorizer(sparse=False), StandardScaler(), Ridge(alpha=float(name.split("_")[1]))
        )
    else:
        model = make_pipeline(
            DictVectorizer(sparse=False),
            GradientBoostingRegressor(
                n_estimators=100,
                max_depth=int(name.split("_")[1]),
                learning_rate=0.05,
                random_state=7,
                loss="squared_error",
            ),
        )
    model.fit([r["features"] for r in training], y)
    return lambda rows: model.predict([r["features"] for r in rows]).tolist()


def evaluate(rows: list[dict]) -> dict:
    result = {}
    for regime in PROTOCOL["spatial_regimes"]:
        result[regime] = {}
        for lead in PROTOCOL["issue_schedules"]:
            result[regime][lead] = {}
            for crop in ("maize", "soybean", "wheat"):
                splits = split_panel(rows, regime, lead, crop)
                cutoff = issue_date(2015, crop, lead).isoformat()
                entry = {
                    "membership": {k: [r["id"] for r in v] for k, v in splits.items()},
                    "target_issue": cutoff,
                    "availability_excluded_pool": [
                        r["id"]
                        for r in rows
                        if r["lead"] == lead
                        and r["year"] in (2011, 2012, 2013)
                        and r["label_available_on"] > cutoff
                    ],
                }
                result[regime][lead][crop] = entry
                if not splits["fit"] or not splits["test"]:
                    entry["status"] = "unavailable_empty_fit_or_test"
                    continue
                if crop not in {r["crop"] for r in splits["fit"]}:
                    entry["status"] = "unavailable_crop_absent_from_fit"
                    continue
                predictors, scores = {}, {}
                candidates = CANDIDATES if splits["select"] else CANDIDATES[:3]
                for name in candidates:
                    predictors[name] = fit_predictor(name, splits["fit"])
                    if splits["select"]:
                        scores[name] = summarize(
                            splits["select"], predictors[name](splits["select"])
                        )
                selected = ["training_mean", "crop_mean", "crop_trend"]
                if scores:
                    for family in ("ridge_", "gbdt_"):
                        selected.append(
                            min(
                                (n for n in CANDIDATES if n.startswith(family)),
                                key=lambda n: (scores[n]["crop_macro_mae"], n),
                            )
                        )
                entry.update(
                    status="evaluated" if scores else "insufficient_selection_baselines_only",
                    validation=scores,
                    selected=selected,
                    best_by_validation=(
                        min(selected, key=lambda n: (scores[n]["crop_macro_mae"], n))
                        if scores
                        else None
                    ),
                    test={},
                )
                for name in selected:
                    predictions = predictors[name](splits["test"])
                    entry["test"][name] = dict(
                        metrics=summarize(splits["test"], predictions),
                        predictions=[
                            [r["id"], p] for r, p in zip(splits["test"], predictions, strict=True)
                        ],
                    )
    return result


def run(data_dir: Path, cache_dir: Path, output: Path, protocol_path: Path) -> dict:
    if protocol_path.resolve() == output.resolve():
        raise ValueError("Protocol and report paths must differ")
    # Create exclusively or verify identical contents; never silently rewrite a frozen protocol.
    protocol_path.parent.mkdir(parents=True, exist_ok=True)
    if protocol_path.exists():
        if json.loads(protocol_path.read_text()) != PROTOCOL:
            raise ValueError("Frozen protocol differs from implementation")
    else:
        with protocol_path.open("x") as stream:
            stream.write(json.dumps(PROTOCOL, indent=2, sort_keys=True) + "\n")
    rows, audit = build_panel(data_dir, cache_dir)
    if not rows:
        raise ValueError("No eligible empirical rows")
    membership = {}
    feature_hashes = {}
    for row in rows:
        membership[row["id"]] = {
            k: row[k]
            for k in (
                "site",
                "physical_site",
                "state",
                "year",
                "crop",
                "yield_t_ha",
                "sowing_date",
                "harvest_date",
                "label_available_on",
            )
        }
        feature_hashes[row["id"] + "|" + row["lead"]] = canonical_hash(row["features"])
    report = dict(
        protocol=PROTOCOL,
        protocol_sha256=canonical_hash(PROTOCOL),
        audit=audit,
        membership=membership,
        feature_sha256=feature_hashes,
        environment={"numpy": np.__version__, "scikit_learn": sklearn.__version__},
        implementation_sha256={
            Path(__file__).name: hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "global_data.py": hashlib.sha256(Path(global_data.__file__).read_bytes()).hexdigest(),
        },
        results=evaluate(rows),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    args = parser.parse_args()
    run(args.data_dir, args.cache_dir, args.output, args.protocol)


if __name__ == "__main__":
    main()
