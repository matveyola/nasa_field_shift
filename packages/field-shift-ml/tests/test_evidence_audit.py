"""Report reanalysis is not raw-data validation or a model release."""

import copy
import json
from pathlib import Path

import pytest

from field_shift_ml.audit_model_evidence import (
    EvidenceError,
    aggregate,
    audit_iowa,
    build_audit,
    checked_metrics,
    comparison,
    farm_bootstrap,
)

REPORTS = Path(__file__).parents[1] / "src/field_shift_ml/evaluations"
ARTIFACTS = Path(__file__).parents[3] / "apps/api/src/field_shift/modules"


def test_equal_farm_rmse_is_not_mean_of_rmse():
    farms = [
        {"m": {"mae": 0.0, "rmse": 0.0, "bias": 0.0}},
        {"m": {"mae": 4.0, "rmse": 4.0, "bias": 4.0}},
    ]
    result = aggregate(farms, "m")
    assert result["mae"] == 2.0
    assert result["rmse"] == pytest.approx(8**0.5)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0, True])
def test_invalid_error_metrics_are_rejected(value):
    with pytest.raises(EvidenceError):
        checked_metrics({"mae": value, "rmse": 1.0, "bias": 0.0})


def test_zero_baseline_does_not_divide_or_invent_percentage():
    result = comparison(
        {"mae": 1.0, "rmse": 1.0, "bias": 0.0}, {"mae": 0.0, "rmse": 0.0, "bias": 0.0}
    )
    assert result["relative_error_reduction_percent"]["mae"] is None
    assert result["candidate_minus_reference"]["mae"] == 1.0


def test_actual_iowa_cohort_reproduces_frozen_summary_and_retains_exclusions():
    report = audit_iowa(REPORTS / "iowa-fields-v2.json", replicates=200)
    assert report["farms"] == 7 and report["paired_days"] == 1680
    assert report["excluded"] == {"IFC:NSHNBTNA04": 24}
    assert report["v2_vs_era5"]["candidate"]["mae"] == pytest.approx(0.056807764064595215)
    assert report["v2_vs_era5"]["reference"]["rmse"] == pytest.approx(0.0736506176823769)
    assert report["serving_scope"]["bootstrap"] is None
    assert report["can_promote"] is False


def test_cohort_count_mismatch_cannot_be_silently_pooled(tmp_path):
    raw = json.loads((REPORTS / "iowa-fields-v2.json").read_text())
    raw["summary"]["days"] += 1
    path = tmp_path / "mismatch.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(EvidenceError, match="cohort counts"):
        audit_iowa(path, replicates=200)


def test_summary_disagreement_requires_investigation_not_reweighting(tmp_path):
    raw = json.loads((REPORTS / "iowa-fields-v2.json").read_text())
    raw["summary"]["model"]["rmse"] += 0.01
    path = tmp_path / "mismatch.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(EvidenceError, match="cannot be reproduced"):
        audit_iowa(path, replicates=200)


def test_bootstrap_is_paired_deterministic_and_not_predictive_interval():
    farms = [
        {
            "a": {"mae": v, "rmse": v, "bias": 0.0},
            "b": {"mae": v + 1.0, "rmse": v + 1.0, "bias": 0.0},
        }
        for v in [1.0, 2.0, 3.0]
    ]
    result = farm_bootstrap(farms, "a", "b", replicates=200)
    assert result == farm_bootstrap(copy.deepcopy(farms), "a", "b", replicates=200)
    # Floating arithmetic introduces ~1e-16 roundoff in paired means.
    assert result["intervals"]["mae"] == pytest.approx([-1.0, -1.0], abs=1e-12)
    assert result["status"] == "report_level_diagnostic"
    assert farm_bootstrap(farms[:2], "a", "b", replicates=200)["intervals"] is None


def test_cross_task_audit_keeps_release_and_units_separate():
    report = build_audit(REPORTS, ARTIFACTS, replicates=200)
    assert report["changes_release_state"] is False
    assert report["rotation_effect"]["task"] == "rotation_yield_effect_not_absolute_yield"
    assert report["rotation_effect"]["unit"] == "t/ha"
    assert report["root_zone_gate"]["recorded_promoted"] is False
    assert report["new_crop_engine_field_accuracy"]["metrics"] is None
