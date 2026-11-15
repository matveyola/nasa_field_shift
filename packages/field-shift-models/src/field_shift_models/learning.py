"""Offline ingestion, leakage barriers, bounded calibration and residual baselines.

No call in this module changes serving models or existing release gates.
"""

from dataclasses import dataclass
from datetime import datetime
from math import asin, cos, radians, sin, sqrt

import numpy as np
from field_shift_contracts.planning_v1.models import TrainingObservation
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .provenance import digest


@dataclass(frozen=True)
class Row:
    observation: TrainingObservation
    issue_at: datetime
    predictor_available_at: datetime
    cycle_at: datetime
    forecast_model: str
    forcing_kind: str
    lead_days: int
    latitude: float
    longitude: float
    features: tuple[float | None, ...]
    reference: float
    partition: str

    def __post_init__(self):
        if self.partition not in ("fit", "calibration", "test"):
            raise ValueError("Explicit evaluation partition required")
        if self.forcing_kind not in ("archived_forecast", "perfect_weather"):
            raise ValueError("Reanalysis cannot be relabelled as an archived forecast")
        if any(
            t.utcoffset() is None or t.utcoffset().total_seconds() != 0
            for t in (self.issue_at, self.predictor_available_at, self.cycle_at)
        ):
            raise ValueError("UTC evaluation timestamps required")
        if self.predictor_available_at > self.issue_at or self.cycle_at > self.issue_at:
            raise ValueError("Predictor unavailable at issue time")
        if (
            self.observation.observed_at < self.issue_at
            or self.lead_days != (self.observation.observed_at.date() - self.cycle_at.date()).days
        ):
            raise ValueError("Label chronology/lead does not match pinned forecast cycle")
        if (
            not self.forecast_model
            or not -90 <= self.latitude <= 90
            or not -180 <= self.longitude <= 180
        ):
            raise ValueError("Pinned forecast model and coordinates required")
        if not self.features or any(v is not None and not np.isfinite(v) for v in self.features):
            raise ValueError("Finite features required; use None for missing predictors")
        if not np.isfinite(self.reference):
            raise ValueError("Finite named baseline prediction required")

    @property
    def target(self):
        o = self.observation
        return (
            o.kind,
            o.unit,
            o.depth_top_cm,
            o.depth_bottom_cm,
            o.crop.model_dump_json() if o.crop else None,
            o.harvest_basis.model_dump_json() if o.harvest_basis else None,
            self.lead_days,
        )


def ingest_jsonl(path):
    """Validate canonical observations including depth, QC, permissions and moisture basis."""
    result = []
    with open(path) as source:
        for line in source:
            if line.strip():
                result.append(TrainingObservation.model_validate_json(line))
    ids = [o.observation_id for o in result]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate observation IDs")
    return result


def distance_km(a, b):
    lat1, lat2 = radians(a.latitude), radians(b.latitude)
    dlat, dlon = lat2 - lat1, radians(b.longitude - a.longitude)
    x = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 6371.0088 * 2 * asin(min(1.0, sqrt(x)))


def check_partitions(rows, buffer_km):
    if not np.isfinite(buffer_km) or buffer_km <= 0:
        raise ValueError("Positive geographic buffer required")
    if len(rows) > 10000:
        raise ValueError("Offline row bound is 10000; prepare a grouped cohort manifest")
    ids = [r.observation.observation_id for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate labels")
    for key in ("farm_group_id", "grid_group_id", "geographic_group_id"):
        groups = {}
        for row in rows:
            group = getattr(row.observation, key)
            if group in groups and groups[group] != row.partition:
                raise ValueError(f"{key} overlaps partitions")
            groups[group] = row.partition
    partitions = {p: [r for r in rows if r.partition == p] for p in ("fit", "calibration", "test")}
    years = [{r.observation.observed_at.year for r in partitions[p]} for p in partitions]
    if any(not y for y in years):
        raise ValueError("All three partitions required")
    if not max(years[0]) < min(years[1]) <= max(years[1]) < min(years[2]):
        raise ValueError("Calibration/test must be strictly later years")
    for i, p in enumerate(partitions):
        for q in list(partitions)[i + 1 :]:
            # Repeated observations at one location do not multiply distance checks.
            a = {(r.latitude, r.longitude): r for r in partitions[p]}.values()
            b = list({(r.latitude, r.longitude): r for r in partitions[q]}.values())
            if any(distance_km(x, y) < buffer_km for x in a for y in b):
                raise ValueError("Geographic buffer overlaps partitions")
    return partitions


@dataclass(frozen=True)
class Gate:
    """Pilot protocol inputs, supplied and frozen before labels are opened.

    There are deliberately no universal accuracy thresholds supplied by this package.
    """

    frozen_at: datetime
    min_farms: int
    min_years: int
    min_rows: int
    min_calibration_rows: int
    min_relative_mae_gain: float
    max_cohort_mae_regression: float
    min_coverage: float
    max_interval_width: float
    buffer_km: float
    max_fit_seconds: float

    def __post_init__(self):
        if self.frozen_at.utcoffset() is None or self.frozen_at.utcoffset().total_seconds() != 0:
            raise ValueError("Gate freeze requires UTC")
        if min(self.min_farms, self.min_years, self.min_rows, self.min_calibration_rows) < 2:
            raise ValueError("Independent farms/years and nontrivial cohorts required")
        for v in (
            self.min_relative_mae_gain,
            self.max_cohort_mae_regression,
            self.min_coverage,
            self.max_interval_width,
            self.buffer_km,
            self.max_fit_seconds,
        ):
            if not np.isfinite(v) or v < 0:
                raise ValueError("Invalid frozen gate bound")
        if (
            not 0 < self.min_coverage < 1
            or not 0 <= self.min_relative_mae_gain < 1
            or (self.max_interval_width <= 0 or self.buffer_km <= 0 or self.max_fit_seconds <= 0)
        ):
            raise ValueError("Nonzero calibrated interval/resource bounds required")

    @property
    def checksum(self):
        return digest({**vars(self), "frozen_at": self.frozen_at.isoformat()})


def scores(actual, predicted):
    error = np.asarray(predicted) - actual
    return {
        "mae": float(np.abs(error).mean()),
        "rmse": float(np.sqrt((error**2).mean())),
        "bias": float(error.mean()),
    }


def residual_benchmark(rows, gate, *, holdout_opened_at):
    """Fit both small learners only on fit; intervals only on calibration.

    Synthetic/rejected/missing labels cannot contribute empirical metrics. Callers must
    keep the test manifest sealed until the protocol has been frozen.
    """
    import time

    if holdout_opened_at.utcoffset() is None or holdout_opened_at.utcoffset().total_seconds() != 0:
        raise ValueError("Holdout opening requires UTC")
    if holdout_opened_at <= gate.frozen_at:
        raise ValueError("Freeze protocol before opening holdout")
    usable = [
        r
        for r in rows
        if not r.observation.synthetic
        and r.observation.quality == "valid"
        and r.observation.value is not None
    ]
    if len(usable) < gate.min_rows:
        return {
            "training_status": "insufficient_training_data",
            "promotion": "insufficient_evidence",
            "usable_rows": len(usable),
            "gate_sha256": gate.checksum,
            "metrics": None,
        }
    if len({r.target for r in usable}) != 1 or len({r.forcing_kind for r in usable}) != 1:
        raise ValueError(
            "Separate kind/depth/crop/moisture-basis/lead and forecast/oracle benchmarks"
        )
    if len({len(r.features) for r in usable}) != 1:
        raise ValueError("Feature schema mismatch")
    partitions = check_partitions(usable, gate.buffer_km)
    if len(partitions["calibration"]) < gate.min_calibration_rows or any(
        len({r.observation.farm_group_id for r in group}) < gate.min_farms
        or len({r.observation.observed_at.year for r in group}) < gate.min_years
        for group in partitions.values()
    ):
        return {
            "training_status": "insufficient_training_data",
            "promotion": "insufficient_evidence",
            "usable_rows": len(usable),
            "gate_sha256": gate.checksum,
            "metrics": None,
        }
    x, y, refs = {}, {}, {}
    for p, group in partitions.items():
        x[p] = np.array([[np.nan if v is None else v for v in r.features] for r in group])
        y[p] = np.array([r.observation.value for r in group])
        refs[p] = np.array([r.reference for r in group])
    learners = {
        "ridge": make_pipeline(
            SimpleImputer(keep_empty_features=True), StandardScaler(), Ridge(alpha=10.0)
        ),
        "shallow_boosting": make_pipeline(
            SimpleImputer(keep_empty_features=True),
            HistGradientBoostingRegressor(
                max_iter=50, max_leaf_nodes=4, max_depth=2, random_state=0
            ),
        ),
    }
    reports = {}
    for name, learner in learners.items():
        start = time.perf_counter()
        learner.fit(x["fit"], y["fit"] - refs["fit"])
        runtime = time.perf_counter() - start
        cal = refs["calibration"] + learner.predict(x["calibration"])
        rank = int(np.ceil((len(cal) + 1) * gate.min_coverage))
        if rank > len(cal):
            reports[name] = {"promotion": "insufficient_evidence", "reason": "interval_sample_size"}
            continue
        radius = float(np.sort(np.abs(y["calibration"] - cal))[rank - 1])
        pred = refs["test"] + learner.predict(x["test"])
        baseline = scores(y["test"], refs["test"])
        candidate = scores(y["test"], pred)
        coverage = float((np.abs(y["test"] - pred) <= radius).mean())
        cohorts = {}
        for i, row in enumerate(partitions["test"]):
            o = row.observation
            for key in (
                f"region:{o.region_id}",
                f"farm:{o.farm_group_id}",
                f"year:{o.observed_at.year}",
                f"crop:{o.crop.crop_id if o.crop else 'none'}",
                f"lead:{row.lead_days}",
            ):
                cohorts.setdefault(key, []).append(i)
        cohort_metrics = {
            k: {
                "baseline": scores(y["test"][ix], refs["test"][ix]),
                "candidate": scores(y["test"][ix], pred[ix]),
                "count": len(ix),
            }
            for k, ix in cohorts.items()
        }
        failed = []
        if candidate["mae"] >= baseline["mae"] * (1 - gate.min_relative_mae_gain):
            failed.append("mae_gain")
        if any(
            m["candidate"]["mae"] > m["baseline"]["mae"] + gate.max_cohort_mae_regression
            for m in cohort_metrics.values()
        ):
            failed.append("cohort_regression")
        if coverage < gate.min_coverage:
            failed.append("interval_coverage")
        if 2 * radius > gate.max_interval_width or radius == 0:
            failed.append("interval_width")
        if runtime > gate.max_fit_seconds:
            failed.append("runtime")
        reports[name] = {
            "baseline": baseline,
            "candidate": candidate,
            "cohorts": cohort_metrics,
            "coverage": coverage,
            "interval_width": 2 * radius,
            "fit_seconds": runtime,
            "failed_gates": failed,
            "promotion": "failed" if failed else "passed",
        }
    return {
        "training_status": "trained",
        "gate_sha256": gate.checksum,
        "forcing_kind": usable[0].forcing_kind,
        "target": usable[0].target,
        "learners": reports,
        "serving_changed": False,
    }


def calibrate_one_parameter(rows, values, evaluate, *, parameter_name, bounds):
    """Bounded one-parameter trial search; evaluator calls the frozen ready engine.

    Candidate values are caller-declared physical bounds. Holdout must never be passed
    here. Each trial requires independent observations; result is a candidate, not a release.
    """
    if any(r.partition != "fit" for r in rows):
        raise ValueError("Calibration search may access fit trials only")
    if (
        parameter_name not in ("WP", "Ksat")
        or len(bounds) != 2
        or not (0 < bounds[0] < bounds[1] and all(np.isfinite(v) for v in bounds))
    ):
        raise ValueError("One named positive bounded physical parameter required")
    observations = [r.observation for r in rows]
    eligible = [
        o for o in observations if not o.synthetic and o.quality == "valid" and o.value is not None
    ]
    if (
        len(eligible) < 6
        or len({o.farm_group_id for o in eligible}) < 3
        or (len({o.observed_at.year for o in eligible}) < 2)
    ):
        return {"training_status": "insufficient_training_data", "parameter": None, "metrics": None}
    targets = {
        (
            o.kind,
            o.unit,
            o.depth_top_cm,
            o.depth_bottom_cm,
            o.crop.model_dump_json() if o.crop else None,
            o.harvest_basis.model_dump_json() if o.harvest_basis else None,
        )
        for o in eligible
    }
    if len(targets) != 1:
        raise ValueError("Calibration trials must share one physical target and moisture basis")
    if not 2 <= len(values) <= 21 or any(
        not np.isfinite(v) or not bounds[0] <= v <= bounds[1] for v in values
    ):
        raise ValueError("Predeclare 2–21 finite candidates for one identifiable parameter")
    actual = np.array([o.value for o in eligible])
    trials = []
    for value in values:
        predicted = np.array(evaluate(value, eligible))
        if predicted.shape != actual.shape or not np.isfinite(predicted).all():
            raise ValueError("Calibration engine returned invalid predictions")
        trials.append({"parameter": value, **scores(actual, predicted)})
    generic = np.array(evaluate(None, eligible))
    if generic.shape != actual.shape or not np.isfinite(generic).all():
        raise ValueError("Frozen generic comparator returned invalid predictions")
    best = min(trials, key=lambda t: t["mae"])
    return {
        "training_status": "trained",
        "promotion": "candidate",
        "parameter": best["parameter"],
        "fit_metrics": best,
        "parameter_name": parameter_name,
        "bounds": bounds,
        "frozen_generic_metrics": scores(actual, generic),
        "fit_inputs_sha256": digest([o.model_dump(mode="json") for o in eligible]),
        "trials": trials,
        "independent_validation": False,
    }
