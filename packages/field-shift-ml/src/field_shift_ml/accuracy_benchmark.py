"""Evaluate frozen prediction pairs without fitting, fetching or promoting models.

Input JSONL (one paired observation per line):
  observation_id, farm_group_id, geographic_group_id, grid_group_id, year,
  crop_id (nullable for moisture), region_id, synthetic (boolean), target,
  observed_at, label_available_at, issue_at, cycle_at, predictor_available_at,
  predictor_source_sha256, label_source_sha256, permission,
  observed={status: observed|missing|rejected, value: number|null},
  candidate/reference={model: {model_id,version,artifact_sha256},
    status: available|insufficient_data|unsupported|out_of_domain|error|not_applicable,
    value: number|null, lower: number|null, upper: number|null, reason: string|null,
    issue_at, cycle_at, lead_days, predictor_source_sha256}.

Manifest JSON: schema_version=accuracy-benchmark-v1, dataset_id,
  purpose=independent_holdout|diagnostic_reuse, lineage_known (boolean),
  gate_frozen_at, selection_frozen_at, holdout_opened_at (explicit UTC), target,
  candidate/reference model refs, observation_ids (the complete predeclared row IDs),
  fit/calibration/holdout group declarations,
  gates (all policy fields in GATE_FIELDS, no scientific defaults).
Each group declaration contains farm_group_ids, geographic_group_ids,
  grid_group_ids and years. Holdout declarations must cover every input row.
Target contains task=surface_moisture|root_zone_reserve|yield, unit, depth_top_cm,
  depth_bottom_cm, harvest_basis={basis,moisture_fraction}|null, lead_days,
  mode=operational_forecast|perfect_weather|historical_reconstruction.
The identical target, issue, cycle, lead and forcing-source SHA apply to both
predictions in each pair. Incompatible tasks/depths/bases/leads/modes need
separate manifests and reports; they are never pooled.

Primary comparisons weight farms equally; row-weighted scores and abstention
counts are separately named. Bootstrap CIs resample declared whole farms and
are conditional on those clusters, not proof of geographic independence or
universal coverage. Files/hashes/timestamps cannot prove externally sealed test
access. The operator must provide that audit evidence. Even passing policy only
establishes eligible_for_release_review; this module never promotes an artifact.
Coverage floors apply globally and to each eligible farm/region/crop/year/lead
cohort. Missing labels count against min_label_coverage; the complete observation
ID list prevents silently omitting difficult registered cases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

VERSION = "accuracy-benchmark-v1"
MAX_ROWS, MAX_FARMS, MAX_BYTES = 10_000, 500, 64 * 1024 * 1024
GROUP_KEYS = ("farm_group_ids", "geographic_group_ids", "grid_group_ids")
MODEL_FIELDS = {"model_id", "version", "artifact_sha256"}
TARGET_FIELDS = {
    "task",
    "unit",
    "depth_top_cm",
    "depth_bottom_cm",
    "harvest_basis",
    "lead_days",
    "mode",
}
GATE_FIELDS = {
    "min_farms",
    "min_years",
    "min_pairs",
    "min_label_coverage",
    "min_candidate_coverage",
    "min_reference_coverage",
    "min_common_support_fraction",
    "min_relative_mae_improvement",
    "min_relative_rmse_improvement",
    "max_cohort_mae_regression",
    "require_intervals",
    "min_interval_coverage",
    "max_mean_interval_width",
    "bootstrap_reps",
    "bootstrap_seed",
    "bootstrap_confidence",
    "require_mae_ci_below_zero",
}
STATUSES = {
    "available",
    "insufficient_data",
    "unsupported",
    "out_of_domain",
    "error",
    "not_applicable",
}


def _object(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f"{label} requires exactly: {', '.join(sorted(fields))}")
    return value


def _id(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise ValueError(f"Invalid {label}")
    return value


def _sha(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError(f"{label} must be a SHA-256 hexadecimal checksum")


def _number(value, label, low=0, high=math.inf, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    if not low <= value <= high or (integer and type(value) is not int):
        raise ValueError(f"{label} is outside its declared bounds")
    return value


def _boolean(value, label):
    if type(value) is not bool:
        raise ValueError(f"{label} must be an explicit boolean")


def _utc(value, label):
    try:
        timestamp = datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} requires an explicit UTC timestamp") from error
    if timestamp.utcoffset() != timedelta(0):
        raise ValueError(f"{label} requires an explicit UTC timestamp")
    return timestamp


def _model(value):
    _object(value, MODEL_FIELDS, "model")
    _id(value["model_id"], "model_id")
    _id(value["version"], "model version")
    _sha(value["artifact_sha256"], "model artifact")


def _target(value):
    _object(value, TARGET_FIELDS, "target")
    task, mode = value["task"], value["mode"]
    if task not in ("surface_moisture", "root_zone_reserve", "yield") or mode not in (
        "operational_forecast",
        "perfect_weather",
        "historical_reconstruction",
    ):
        raise ValueError("Unknown task or evaluation mode")
    _number(value["lead_days"], "lead_days", 0, 2192, integer=True)
    if mode == "historical_reconstruction" and value["lead_days"] != 0:
        raise ValueError("Historical reconstruction is not a forecast lead")
    unit = {"surface_moisture": "m3/m3", "root_zone_reserve": "mm", "yield": "t/ha"}[task]
    if value["unit"] != unit:
        raise ValueError("Task/unit mismatch")
    if task == "yield":
        if value["depth_top_cm"] is not None or value["depth_bottom_cm"] is not None:
            raise ValueError("Yield has harvest basis, not a soil depth")
        basis = _object(value["harvest_basis"], {"basis", "moisture_fraction"}, "harvest_basis")
        moisture = _number(basis["moisture_fraction"], "harvest moisture fraction", 0, 1)
        if basis["basis"] not in ("dry_matter", "standard_moisture", "fresh_weight") or (
            moisture == 1 or (basis["basis"] == "dry_matter" and moisture != 0)
        ):
            raise ValueError("Invalid harvest basis")
    else:
        top = _number(value["depth_top_cm"], "depth_top_cm", 0, 1000)
        bottom = _number(value["depth_bottom_cm"], "depth_bottom_cm", 0, 1000)
        if top >= bottom or value["harvest_basis"] is not None:
            raise ValueError("Moisture/reserve needs an explicit depth interval, not harvest basis")


def _physical(value, target, label):
    limit = 1 if target["task"] == "surface_moisture" else math.inf
    if target["task"] == "root_zone_reserve":
        limit = (target["depth_bottom_cm"] - target["depth_top_cm"]) * 10
    return _number(value, label, 0, limit)


def _groups(value, label):
    _object(value, {*GROUP_KEYS, "years"}, label)
    for key in GROUP_KEYS:
        groups = value[key]
        if not isinstance(groups, list) or len(groups) > MAX_ROWS:
            raise ValueError(f"Invalid {label} {key}")
        for group in groups:
            _id(group, key)
        if len(set(groups)) != len(groups):
            raise ValueError(f"Duplicate {label} {key}")
    years = value["years"]
    if not isinstance(years, list):
        raise ValueError(f"Invalid {label} years")
    for year in years:
        _number(year, f"{label} year", 1900, 2200, integer=True)
    if len(set(years)) != len(years):
        raise ValueError(f"Duplicate {label} years")


def validate_manifest(manifest):
    _object(
        manifest,
        {
            "schema_version",
            "dataset_id",
            "purpose",
            "lineage_known",
            "gate_frozen_at",
            "selection_frozen_at",
            "holdout_opened_at",
            "target",
            "candidate",
            "reference",
            "fit",
            "calibration",
            "holdout",
            "gates",
            "observation_ids",
        },
        "manifest",
    )
    if manifest["schema_version"] != VERSION or manifest["purpose"] not in (
        "independent_holdout",
        "diagnostic_reuse",
    ):
        raise ValueError("Unknown manifest version/purpose")
    _id(manifest["dataset_id"], "dataset_id")
    ids = manifest["observation_ids"]
    if not isinstance(ids, list) or len(ids) > MAX_ROWS:
        raise ValueError("Declare the bounded complete observation ID list")
    for identifier in ids:
        _id(identifier, "manifest observation_id")
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate manifest observation_id")
    _boolean(manifest["lineage_known"], "lineage_known")
    _target(manifest["target"])
    for name in ("candidate", "reference"):
        _model(manifest[name])
    for name in ("fit", "calibration", "holdout"):
        _groups(manifest[name], name)
    if not manifest["holdout"]["years"]:
        raise ValueError("Declare held-out years before evaluation")
    gates = _object(manifest["gates"], GATE_FIELDS, "gates")
    for key, lo, hi in (
        ("min_farms", 2, MAX_FARMS),
        ("min_years", 1, 200),
        ("min_pairs", 1, MAX_ROWS),
        ("bootstrap_reps", 100, 2000),
        ("bootstrap_seed", 0, 2**32 - 1),
    ):
        _number(gates[key], key, lo, hi, integer=True)
    for key in (
        "min_label_coverage",
        "min_candidate_coverage",
        "min_reference_coverage",
        "min_common_support_fraction",
        "min_relative_mae_improvement",
        "min_relative_rmse_improvement",
    ):
        _number(gates[key], key, 0, 1)
    _number(gates["max_cohort_mae_regression"], "max_cohort_mae_regression")
    confidence = _number(gates["bootstrap_confidence"], "bootstrap_confidence", 0, 1)
    if confidence in (0, 1):
        raise ValueError("Bootstrap confidence must be strictly between zero and one")
    for key in ("require_intervals", "require_mae_ci_below_zero"):
        _boolean(gates[key], key)
    for key in ("min_interval_coverage", "max_mean_interval_width"):
        v = gates[key]
        if v is not None:
            _number(v, key, 0, 1 if key == "min_interval_coverage" else math.inf)
    if gates["require_intervals"] and (
        gates["min_interval_coverage"] is None or not gates["max_mean_interval_width"]
    ):
        raise ValueError("Required intervals need predeclared coverage and positive width ceiling")
    violations = []
    for phase in ("fit", "calibration"):
        data = manifest[phase]
        if any(data[key] for key in GROUP_KEYS) and not data["years"]:
            violations.append(f"{phase}_years_missing")
        if data["years"] and any(not data[key] for key in GROUP_KEYS):
            violations.append(f"{phase}_groups_missing")
    opened = _utc(manifest["holdout_opened_at"], "holdout_opened_at")
    for key in ("gate_frozen_at", "selection_frozen_at"):
        if _utc(manifest[key], key) >= opened:
            violations.append(f"{key}_not_before_holdout")
    for key in GROUP_KEYS:
        a, b, c = (set(manifest[p][key]) for p in ("fit", "calibration", "holdout"))
        if a & b or a & c or b & c:
            violations.append(f"overlapping_{key}")
    phases = [manifest[p]["years"] for p in ("fit", "calibration", "holdout")]
    nonempty = [years for years in phases if years]
    if any(max(a) >= min(b) for a, b in zip(nonempty, nonempty[1:])):
        violations.append("non_future_holdout_years")
    if manifest["purpose"] == "independent_holdout" and violations:
        raise ValueError("Independent evaluation leakage: " + ", ".join(violations))
    return violations


def validate_row(row, manifest):
    _object(
        row,
        {
            "observation_id",
            "farm_group_id",
            "geographic_group_id",
            "grid_group_id",
            "year",
            "crop_id",
            "region_id",
            "synthetic",
            "target",
            "observed_at",
            "label_available_at",
            "issue_at",
            "cycle_at",
            "predictor_available_at",
            "predictor_source_sha256",
            "label_source_sha256",
            "permission",
            "observed",
            "candidate",
            "reference",
        },
        "prediction row",
    )
    for key in (
        "observation_id",
        "farm_group_id",
        "geographic_group_id",
        "grid_group_id",
        "region_id",
    ):
        _id(row[key], key)
    if row["crop_id"] is not None:
        _id(row["crop_id"], "crop_id")
    elif manifest["target"]["task"] == "yield":
        raise ValueError("Yield evaluation requires crop_id")
    _boolean(row["synthetic"], "synthetic")
    _target(row["target"])
    if row["target"] != manifest["target"]:
        raise ValueError("Incompatible target/depth/basis/lead/mode; use a separate report")
    _number(row["year"], "year", 1900, 2200, integer=True)
    for key in GROUP_KEYS:
        if row[key.removesuffix("s")] not in manifest["holdout"][key]:
            raise ValueError(f"Row outside predeclared holdout {key}")
    if row["year"] not in manifest["holdout"]["years"]:
        raise ValueError("Row outside held-out years")
    for key in ("label_source_sha256", "predictor_source_sha256"):
        _sha(row[key], key)
    if not isinstance(row["permission"], str) or not 0 < len(row["permission"].strip()) <= 2000:
        raise ValueError("Label/data permission evidence is required")
    observed, available, issue, cycle, predictor = (
        _utc(row[k], k)
        for k in (
            "observed_at",
            "label_available_at",
            "issue_at",
            "cycle_at",
            "predictor_available_at",
        )
    )
    if observed > available or observed.year != row["year"] or cycle > issue:
        raise ValueError("Invalid observation or cycle chronology")
    mode = row["target"]["mode"]
    if mode == "operational_forecast" and (predictor > issue or issue > observed):
        raise ValueError("Operational forecast predictors were unavailable at issue time")
    if mode == "historical_reconstruction" and (predictor > issue or observed > issue):
        raise ValueError("Invalid historical reconstruction chronology")
    if mode != "historical_reconstruction" and (
        issue > observed or row["target"]["lead_days"] != (observed.date() - cycle.date()).days
    ):
        raise ValueError("Forecast/oracle lead does not match observation and cycle")
    label = _object(row["observed"], {"status", "value"}, "observed")
    if label["status"] == "observed":
        _physical(label["value"], row["target"], "observed value")
    elif label["status"] not in ("missing", "rejected") or label["value"] is not None:
        raise ValueError("Missing/rejected labels must be explicit nulls")
    for name in ("candidate", "reference"):
        pred = _object(
            row[name],
            {
                "model",
                "status",
                "value",
                "lower",
                "upper",
                "reason",
                "issue_at",
                "cycle_at",
                "lead_days",
                "predictor_source_sha256",
            },
            name,
        )
        _model(pred["model"])
        if pred["model"] != manifest[name]:
            raise ValueError("Prediction model/version/artifact differs from frozen selection")
        if not isinstance(pred["status"], str) or pred["status"] not in STATUSES:
            raise ValueError("Unknown prediction status")
        if (
            _utc(pred["issue_at"], "prediction issue_at") != issue
            or (_utc(pred["cycle_at"], "prediction cycle_at") != cycle)
            or pred["lead_days"] != row["target"]["lead_days"]
            or (pred["predictor_source_sha256"] != row["predictor_source_sha256"])
        ):
            raise ValueError(
                "Candidate/reference must share exact issue/cycle/lead/forcing support"
            )
        _number(pred["lead_days"], "prediction lead_days", 0, 2192, integer=True)
        if pred["status"] != "available":
            if any(pred[k] is not None for k in ("value", "lower", "upper")) or not (
                isinstance(pred["reason"], str) and pred["reason"].strip()
            ):
                raise ValueError("Abstention needs null values and an explicit reason")
            continue
        _physical(pred["value"], row["target"], name)
        if pred["reason"] is not None and not isinstance(pred["reason"], str):
            raise ValueError("Prediction reason must be text or null")
        if (pred["lower"] is None) != (pred["upper"] is None):
            raise ValueError("Both interval bounds are required")
        if pred["lower"] is not None:
            _physical(pred["lower"], row["target"], "interval lower")
            _physical(pred["upper"], row["target"], "interval upper")
            if not pred["lower"] <= pred["value"] <= pred["upper"]:
                raise ValueError("Interval must contain its point prediction")


def _mean(values):
    return math.fsum(v / len(values) for v in values)


def _scores(rows, name):
    if not rows:
        return None
    errors = [r[name]["value"] - r["observed"]["value"] for r in rows]
    return {
        "mae": _mean([abs(e) for e in errors]),
        "rmse": math.hypot(*errors) / math.sqrt(len(errors)),
        "bias": _mean(errors),
    }


def _intervals(rows, name):
    intervals = [r for r in rows if r[name]["lower"] is not None]
    return {
        "count": len(intervals),
        "fraction_of_scored": len(intervals) / len(rows) if rows else 0.0,
        "coverage": _mean(
            [
                float(r[name]["lower"] <= r["observed"]["value"] <= r[name]["upper"])
                for r in intervals
            ]
        )
        if intervals
        else None,
        "mean_width": _mean([r[name]["upper"] - r[name]["lower"] for r in intervals])
        if intervals
        else None,
    }


def _cohort_keys(row):
    return [f"{key}:{row[key]}" for key in ("farm_group_id", "region_id", "crop_id", "year")] + [
        f"lead_days:{row['target']['lead_days']}"
    ]


def _support(rows):
    count = len(rows)
    candidate = sum(r["candidate"]["status"] == "available" for r in rows)
    reference = sum(r["reference"]["status"] == "available" for r in rows)
    common = sum(r["candidate"]["status"] == r["reference"]["status"] == "available" for r in rows)
    return {
        "eligible_labels": count,
        "candidate_available": candidate,
        "reference_available": reference,
        "common_pairs": common,
        "candidate_coverage": candidate / count,
        "reference_coverage": reference / count,
        "common_support_fraction": common / count,
    }


def _farm_scores(rows, name):
    groups = defaultdict(list)
    for r in rows:
        groups[r["farm_group_id"]].append(r)
    scores = [_scores(group, name) for group in groups.values()]
    if not scores:
        return None
    return {
        "mae": _mean([s["mae"] for s in scores]),
        "rmse": math.hypot(*(s["rmse"] for s in scores)) / math.sqrt(len(scores)),
        "bias": _mean([s["bias"] for s in scores]),
    }


def _quantile(values, fraction):
    values = sorted(values)
    index = (len(values) - 1) * fraction
    lower = int(index)
    fraction = index - lower
    return (1 - fraction) * values[lower] + fraction * values[min(lower + 1, len(values) - 1)]


def _bootstrap(rows, gates):
    groups = defaultdict(list)
    for r in rows:
        groups[r["farm_group_id"]].append(r)
    if len(groups) < 2:
        return None
    clusters = [[_scores(groups[k], n) for n in ("candidate", "reference")] for k in sorted(groups)]
    rng = random.Random(gates["bootstrap_seed"])
    mae, rmse = [], []
    for _ in range(gates["bootstrap_reps"]):
        picked = rng.choices(clusters, k=len(clusters))
        mae.append(_mean([a["mae"] - b["mae"] for a, b in picked]))
        rmse.append(
            math.hypot(*(a["rmse"] for a, _ in picked)) / math.sqrt(len(picked))
            - math.hypot(*(b["rmse"] for _, b in picked)) / math.sqrt(len(picked))
        )
    alpha = (1 - gates["bootstrap_confidence"]) / 2
    point = [_farm_scores(rows, n) for n in ("candidate", "reference")]
    return {
        "method": "paired_declared_farm_cluster_percentile",
        "seed": gates["bootstrap_seed"],
        "reps": gates["bootstrap_reps"],
        "confidence": gates["bootstrap_confidence"],
        "clusters": len(clusters),
        "weighting": "equal_farm",
        "direction": "candidate_minus_reference",
        "mae_difference": point[0]["mae"] - point[1]["mae"],
        "mae_difference_ci": [_quantile(mae, alpha), _quantile(mae, 1 - alpha)],
        "rmse_difference": point[0]["rmse"] - point[1]["rmse"],
        "rmse_difference_ci": [_quantile(rmse, alpha), _quantile(rmse, 1 - alpha)],
        "assumption": "Conditional on declared farm clusters; overlapping weather/geography can "
        "violate independence. No global or geographic coverage claim.",
    }


def canonical_sha(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def evaluate(rows, manifest):
    """Validate all rows before excluding synthetic/missing labels from empirical metrics."""
    violations = validate_manifest(manifest)
    if len(rows) > MAX_ROWS:
        raise ValueError(f"At most {MAX_ROWS} rows per benchmark")
    identifiers = set()
    for row in rows:
        validate_row(row, manifest)
        if row["observation_id"] in identifiers:
            raise ValueError("Duplicate observation_id; select one exact issue/lead per report")
        identifiers.add(row["observation_id"])
    if identifiers != set(manifest["observation_ids"]):
        raise ValueError(
            "Rows must exactly cover predeclared observation_ids; retain missing/abstention rows"
        )
    if len({r["farm_group_id"] for r in rows}) > MAX_FARMS:
        raise ValueError(f"At most {MAX_FARMS} farm clusters per benchmark")
    real = [r for r in rows if not r["synthetic"]]
    eligible = [r for r in real if r["observed"]["status"] == "observed"]
    available = {
        n: [r for r in eligible if r[n]["status"] == "available"]
        for n in ("candidate", "reference")
    }
    common = [r for r in eligible if all(r[n]["status"] == "available" for n in available)]
    counts = {
        "total_rows": len(rows),
        "synthetic_rows_excluded": len(rows) - len(real),
        "real_rows": len(real),
        "eligible_observed_labels": len(eligible),
        "label_coverage": len(eligible) / len(real) if real else 0.0,
        "missing_labels": sum(r["observed"]["status"] == "missing" for r in real),
        "rejected_labels": sum(r["observed"]["status"] == "rejected" for r in real),
        "common_support_pairs": len(common),
        "common_support_fraction": len(common) / len(eligible) if eligible else 0.0,
        "common_support_farms": len({r["farm_group_id"] for r in common}),
        "common_support_years": len({r["year"] for r in common}),
    }
    models = {}
    for name in available:
        models[name] = {
            "available": len(available[name]),
            "abstention_statuses": dict(
                sorted(
                    Counter(
                        r[name]["status"] for r in eligible if r[name]["status"] != "available"
                    ).items()
                )
            ),
            "coverage": len(available[name]) / len(eligible) if eligible else 0.0,
            "own_available_support_metrics": _scores(available[name], name),
            "intervals_on_own_support": _intervals(available[name], name),
        }
    metrics = {n: _scores(common, n) for n in available} if common else None
    balanced = {n: _farm_scores(common, n) for n in available} if common else None
    cohort_rows, coverage_rows = defaultdict(list), defaultdict(list)
    for row in eligible:
        for key in _cohort_keys(row):
            coverage_rows[key].append(row)
    cohort_coverage = {k: _support(v) for k, v in sorted(coverage_rows.items())}
    for row in common:
        for key in _cohort_keys(row):
            cohort_rows[key].append(row)
    cohorts = {
        k: {"count": len(v), **{n: _scores(v, n) for n in available}}
        for k, v in sorted(cohort_rows.items())
    }
    gates = manifest["gates"]
    bootstrap = _bootstrap(common, gates)
    intervals = _intervals(common, "candidate")
    checks = {
        "known_lineage": manifest["lineage_known"],
        "independent_holdout": manifest["purpose"] == "independent_holdout" and not violations,
        "minimum_pairs": len(common) >= gates["min_pairs"],
        "minimum_farms": counts["common_support_farms"] >= gates["min_farms"],
        "minimum_years": counts["common_support_years"] >= gates["min_years"],
        "label_coverage": counts["label_coverage"] >= gates["min_label_coverage"],
        "candidate_coverage": models["candidate"]["coverage"] >= gates["min_candidate_coverage"],
        "reference_coverage": models["reference"]["coverage"] >= gates["min_reference_coverage"],
        "common_support_coverage": counts["common_support_fraction"]
        >= gates["min_common_support_fraction"],
        "cohort_support_coverage": all(
            c["candidate_coverage"] >= gates["min_candidate_coverage"]
            and c["reference_coverage"] >= gates["min_reference_coverage"]
            and c["common_support_fraction"] >= gates["min_common_support_fraction"]
            for c in cohort_coverage.values()
        )
        if cohort_coverage
        else False,
        "mae_improvement": balanced["candidate"]["mae"] < balanced["reference"]["mae"]
        and balanced["candidate"]["mae"]
        <= balanced["reference"]["mae"] * (1 - gates["min_relative_mae_improvement"])
        if balanced
        else None,
        "rmse_improvement": balanced["candidate"]["rmse"] < balanced["reference"]["rmse"]
        and balanced["candidate"]["rmse"]
        <= balanced["reference"]["rmse"] * (1 - gates["min_relative_rmse_improvement"])
        if balanced
        else None,
        "cohort_non_regression": all(
            c["candidate"]["mae"] <= c["reference"]["mae"] + gates["max_cohort_mae_regression"]
            for c in cohorts.values()
        )
        if cohorts
        else None,
        "mae_difference_ci": (bootstrap is not None and bootstrap["mae_difference_ci"][1] < 0)
        if gates["require_mae_ci_below_zero"]
        else True,
        "interval_support": intervals["count"] == len(common) and bool(common)
        if gates["require_intervals"]
        or gates["min_interval_coverage"] is not None
        or gates["max_mean_interval_width"] is not None
        else True,
        "interval_coverage": intervals["coverage"] is not None
        and intervals["coverage"] >= gates["min_interval_coverage"]
        if gates["min_interval_coverage"] is not None
        else True,
        "interval_width": intervals["mean_width"] is not None
        and 0 < intervals["mean_width"] <= gates["max_mean_interval_width"]
        if gates["max_mean_interval_width"] is not None
        else True,
    }
    enough = all(checks[k] for k in ("minimum_pairs", "minimum_farms", "minimum_years"))
    report = {
        "schema_version": VERSION,
        "dataset_id": manifest["dataset_id"],
        "target": manifest["target"],
        "candidate": manifest["candidate"],
        "reference": manifest["reference"],
        "purpose": manifest["purpose"],
        "status": "evaluated" if enough else "insufficient_data",
        "counts": counts,
        "model_support": models,
        "row_weighted_common_support_metrics": metrics,
        "equal_farm_common_support_metrics": balanced,
        "cohorts_on_common_support": cohorts,
        "cohort_coverage_on_eligible_labels": cohort_coverage,
        "candidate_intervals_on_common_support": intervals,
        "paired_farm_bootstrap": bootstrap,
        "policy": gates,
        "gates": checks,
        "failed_gates": [k for k, v in checks.items() if v is False],
        "not_run_gates": [k for k, v in checks.items() if v is None],
        "declared_lineage_violations": violations,
        "eligible_for_release_review": all(v is True for v in checks.values()),
        "promotion": "not_performed",
        "serving_changed": False,
        "predictions_sha256": canonical_sha(rows),
        "manifest_sha256": canonical_sha(manifest),
        "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "limits": {
            "max_rows": MAX_ROWS,
            "max_farms": MAX_FARMS,
            "max_input_bytes": MAX_BYTES,
            "max_bootstrap_reps": 2000,
        },
        "audit_requirement": "Hashes and JSON dates do not prove externally sealed test access; "
        "operator audit required. Synthetic rows never count as empirical evidence.",
    }
    report["report_sha256"] = canonical_sha(report)
    return report


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key {key!r}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"Invalid JSON {value}")


def _json(raw):
    return json.loads(
        raw,
        object_pairs_hook=_unique_object,
        parse_constant=_invalid_constant,
    )


def evaluate_files(predictions: Path, manifest: Path):
    if not predictions.is_file() or not manifest.is_file():
        raise ValueError("Prediction and manifest paths must be regular files")
    if predictions.stat().st_size + manifest.stat().st_size > MAX_BYTES:
        raise ValueError("Prediction/manifest inputs exceed 64 MiB")
    with predictions.open("rb") as source:
        raw = source.read(MAX_BYTES + 1)
    with manifest.open("rb") as source:
        meta = source.read(max(0, MAX_BYTES - len(raw)) + 1)
    if len(raw) + len(meta) > MAX_BYTES:
        raise ValueError("Prediction/manifest inputs exceed 64 MiB")
    rows = []
    for line in raw.splitlines():
        if line.strip():
            if len(rows) >= MAX_ROWS:
                raise ValueError(f"At most {MAX_ROWS} rows per benchmark")
            rows.append(_json(line))
    result = evaluate(rows, _json(meta))
    result["input_files_sha256"] = {
        "predictions": hashlib.sha256(raw).hexdigest(),
        "manifest": hashlib.sha256(meta).hexdigest(),
    }
    result.pop("report_sha256")
    result["report_sha256"] = canonical_sha(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.resolve() in {args.predictions.resolve(), args.manifest.resolve()}:
            raise ValueError("Output must not overwrite either evaluation input")
        result = evaluate_files(args.predictions, args.manifest)
        payload = json.dumps(result, indent=2, allow_nan=False) + "\n"
        name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=args.output.parent,
                prefix=".accuracy-",
                delete=False,
            ) as temporary:
                name = temporary.name
                temporary.write(payload)
            os.replace(name, args.output)
        finally:
            if name is not None:
                Path(name).unlink(missing_ok=True)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(
        f"{result['status']}: {result['counts']['common_support_pairs']} paired observations; "
        f"eligible_for_release_review={result['eligible_for_release_review']}"
    )


if __name__ == "__main__":
    main()
