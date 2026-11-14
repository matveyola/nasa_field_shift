"""Reanalyse frozen reports without pretending to rerun inference or open a new holdout."""

import argparse
import hashlib
import json
import math
import random
from pathlib import Path
from statistics import mean


class EvidenceError(ValueError):
    pass


def read_report(path):
    raw = path.read_bytes()
    if len(raw) > 8 * 1024 * 1024:
        raise EvidenceError("Report exceeds 8 MiB")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise EvidenceError("Report must be a JSON object")
    return value, hashlib.sha256(raw).hexdigest()


def checked_metrics(value):
    if not isinstance(value, dict) or not {"mae", "rmse", "bias"} <= value.keys():
        raise EvidenceError("Complete MAE/RMSE/bias required")
    result = {}
    for key in ("mae", "rmse", "bias"):
        x = value[key]
        if type(x) not in (int, float) or not math.isfinite(x):
            raise EvidenceError("Metrics must be finite numbers")
        result[key] = float(x)
    if (
        result["mae"] < 0
        or result["rmse"] + 1e-9 < result["mae"]
        or (abs(result["bias"]) > result["mae"] + 1e-9)
    ):
        raise EvidenceError("Incoherent error metrics")
    return result


def aggregate(farms, model):
    metrics = [checked_metrics(f[model]) for f in farms]
    return {
        "mae": mean(m["mae"] for m in metrics),
        "rmse": math.sqrt(mean(m["rmse"] ** 2 for m in metrics)),
        "bias": mean(m["bias"] for m in metrics),
    }


def comparison(candidate, reference):
    candidate, reference = checked_metrics(candidate), checked_metrics(reference)
    return {
        "candidate": candidate,
        "reference": reference,
        "candidate_minus_reference": {k: candidate[k] - reference[k] for k in candidate},
        "relative_error_reduction_percent": {
            k: 100 * (reference[k] - candidate[k]) / reference[k] if reference[k] else None
            for k in ("mae", "rmse")
        },
        "direction": "Negative error difference / positive error reduction favors candidate; "
        "bias alone is not an accuracy ranking",
    }


def quantile(values, q):
    values = sorted(values)
    position = (len(values) - 1) * q
    lo, hi = math.floor(position), math.ceil(position)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def farm_bootstrap(farms, candidate, reference, *, replicates=5000, seed=20261114):
    if type(replicates) is not int or not 200 <= replicates <= 10000:
        raise EvidenceError("Bootstrap bound is 200–10000 replicates")
    if len(farms) < 3:
        return {"status": "insufficient_data", "intervals": None}
    rng = random.Random(seed)
    deltas = {"mae": [], "rmse": []}
    for _ in range(replicates):
        sample = [farms[rng.randrange(len(farms))] for _ in farms]
        c, r = aggregate(sample, candidate), aggregate(sample, reference)
        for key in deltas:
            deltas[key].append(c[key] - r[key])
    return {
        "status": "report_level_diagnostic",
        "method": "paired_farm_percentile_bootstrap_v1",
        "farms": len(farms),
        "replicates": replicates,
        "seed": seed,
        "nominal_level": 0.95,
        "intervals": {k: [quantile(v, 0.025), quantile(v, 0.975)] for k, v in deltas.items()},
        "limitations": "Conditional on these already-scored farms and rounded report metrics. "
        "No within-farm resampling; spatial/cross-farm dependence and sampling bias are not "
        "resolved. Not a field predictive interval or evidence of new geographic validity.",
    }


def audit_iowa(path, *, replicates=5000):
    data, checksum = read_report(path)
    excluded = data["insufficient_pair_counts"]
    farms = {name: f for name, f in data["farms"].items() if name not in excluded}
    if not farms:
        raise EvidenceError("No qualified farms")
    if any(type(f["days"]) is not int or f["days"] <= 0 for f in farms.values()):
        raise EvidenceError("Positive farm pair counts required")
    if (
        len(farms) != data["summary"]["site_count"]
        or sum(f["days"] for f in farms.values()) != data["summary"]["days"]
    ):
        raise EvidenceError("Qualified cohort counts disagree with frozen summary")
    verified = {}
    for model in ("era5_land", "v1", "model"):
        actual = aggregate(list(farms.values()), model)
        expected = checked_metrics(data["summary"][model])
        if any(abs(actual[k] - expected[k]) > 1e-9 for k in actual):
            raise EvidenceError("Frozen equal-farm summary cannot be reproduced")
        verified[model] = actual
    per_farm = {
        name: comparison(f["model"], f["era5_land"]) | {"pairs": f["days"]}
        for name, f in sorted(farms.items())
    }
    scope = data["operational_scope_diagnostic"]
    return {
        "source": path.name,
        "source_sha256": checksum,
        "task": "historical_surface_moisture",
        "unit": "m3/m3",
        "mode": "historical_reconstruction_not_operational_forecast",
        "evidence_kind": "reused_frozen_report_not_new_validation",
        "farms": len(farms),
        "paired_days": data["summary"]["days"],
        "excluded": excluded,
        "aggregation": "Equal farm MAE/bias; RMSE=sqrt(mean(farm MSE)); not mean(farm RMSE)",
        "v2_vs_era5": comparison(verified["model"], verified["era5_land"]),
        "v2_vs_v1": comparison(verified["model"], verified["v1"]),
        "per_farm": per_farm,
        "wins_vs_era5": {
            k: sum(f["model"][k] < f["era5_land"][k] for f in farms.values())
            for k in ("mae", "rmse")
        },
        "paired_farm_bootstrap": farm_bootstrap(
            list(farms.values()), "model", "era5_land", replicates=replicates
        ),
        "serving_scope": {
            "farms": scope["site_count"],
            "paired_days": scope["days"],
            **comparison(scope["model"], scope["era5_land"]),
            "bootstrap": None,
            "reason": "Scope-specific per-farm/raw predictions "
            "not recorded; cannot reuse full-period farm scores for this subset",
        },
        "recorded_promotion_checks": data["promotion_checks"],
        "can_promote": False,
    }


def audit_artifact(path, task, unit, metric_path, reference_key, candidate_key):
    artifact, checksum = read_report(path)
    node = artifact
    for key in metric_path:
        node = node[key]
    return {
        "source": path.name,
        "source_sha256": checksum,
        "model_version": artifact["version"],
        "task": task,
        "unit": unit,
        "recorded_promoted": artifact["promoted"],
        "evidence_kind": "reused_artifact_report_not_new_validation",
        **comparison(node[candidate_key], node[reference_key]),
        "can_promote": False,
    }


def build_audit(reports_dir, artifacts_dir, *, replicates=5000):
    historical = audit_iowa(reports_dir / "iowa-fields-v2.json", replicates=replicates)
    forecast = audit_artifact(
        artifacts_dir / "geo_ml/artifacts/moisture-ifs-v1.json",
        "surface_forecast_49R1_leads_1_5",
        "m3/m3",
        ["validation", "independent_farms"],
        "ifs",
        "learned",
    )
    rotation = audit_artifact(
        artifacts_dir / "rotations/artifacts/rotation-yield-v1.json",
        "rotation_yield_effect_not_absolute_yield",
        "t/ha",
        ["validation", "evaluation"],
        "baseline",
        "candidate",
    )
    root, root_hash = read_report(artifacts_dir / "simulation/artifacts/root-zone-ifs-v1.json")
    return {
        "report_version": "field-shift-evidence-audit-v1",
        "purpose": "Reproducible audit of already-opened evidence; no new inference/training",
        "historical_surface": historical,
        "legacy_surface_forecast": forecast,
        "rotation_effect": rotation,
        "root_zone_gate": {
            "source_sha256": root_hash,
            "recorded_promoted": root["promoted"],
            "target": root["validation"]["target"],
            "recorded_gates": root["validation"]["gates"],
        },
        "new_crop_engine_field_accuracy": {
            "status": "insufficient_data",
            "metrics": None,
            "reason": "No matched crop-engine predictions and "
            "independent measured field yields provided to this audit",
        },
        "sota_claim": "not_established_for_our_fields",
        "changes_release_state": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--replicates", type=int, default=5000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_audit(args.reports_dir, args.artifacts_dir, replicates=args.replicates)
    text = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
