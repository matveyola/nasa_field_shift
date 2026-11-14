"""Artificial pair fixtures test evaluation math/guards, not real field performance."""

import copy
import json
import math
import subprocess
import sys

import pytest
from field_shift_ml.accuracy_benchmark import canonical_sha, evaluate, evaluate_files


def inputs():
    target = {
        "task": "yield",
        "unit": "t/ha",
        "depth_top_cm": None,
        "depth_bottom_cm": None,
        "harvest_basis": {"basis": "dry_matter", "moisture_fraction": 0.0},
        "lead_days": 1,
        "mode": "operational_forecast",
    }
    models = {
        name: {"model_id": name, "version": "unit-fixture-v1", "artifact_sha256": digit * 64}
        for name, digit in (("candidate", "1"), ("reference", "2"))
    }

    def groups(prefix, years):
        return {
            "farm_group_ids": [prefix + "F"],
            "geographic_group_ids": [prefix + "G"],
            "grid_group_ids": [prefix + "W"],
            "years": years,
        }

    manifest = {
        "schema_version": "accuracy-benchmark-v1",
        "dataset_id": "unit-fixtures-not-empirical",
        "purpose": "independent_holdout",
        "lineage_known": True,
        "gate_frozen_at": "2023-01-01T00:00:00Z",
        "selection_frozen_at": "2023-01-02T00:00:00Z",
        "holdout_opened_at": "2027-01-01T00:00:00Z",
        "target": target,
        **models,
        "fit": groups("fit", [2020]),
        "calibration": groups("cal", [2022]),
        "holdout": {
            "farm_group_ids": ["F1", "F2"],
            "geographic_group_ids": ["G1", "G2"],
            "grid_group_ids": ["W1", "W2"],
            "years": [2025, 2026],
        },
        "gates": {
            "min_farms": 2,
            "min_years": 2,
            "min_pairs": 4,
            "min_label_coverage": 1.0,
            "min_candidate_coverage": 1.0,
            "min_reference_coverage": 1.0,
            "min_common_support_fraction": 1.0,
            "min_relative_mae_improvement": 0.1,
            "min_relative_rmse_improvement": 0.1,
            "max_cohort_mae_regression": 0.1,
            "require_intervals": False,
            "min_interval_coverage": None,
            "max_mean_interval_width": None,
            "bootstrap_reps": 100,
            "bootstrap_seed": 17,
            "bootstrap_confidence": 0.95,
            "require_mae_ci_below_zero": True,
        },
    }
    rows = []
    for year in [2025, 2026]:
        for index in [1, 2]:
            issue, cycle = f"{year}-05-31T06:00:00Z", f"{year}-05-31T00:00:00Z"
            r = {
                "observation_id": f"F{index}-{year}",
                "farm_group_id": f"F{index}",
                "geographic_group_id": f"G{index}",
                "grid_group_id": f"W{index}",
                "year": year,
                "crop_id": "Maize",
                "region_id": "unit-region",
                "synthetic": False,
                "target": copy.deepcopy(target),
                "observed_at": f"{year}-06-01T00:00:00Z",
                "label_available_at": f"{year}-06-02T00:00:00Z",
                "issue_at": issue,
                "cycle_at": cycle,
                "predictor_available_at": f"{year}-05-31T04:00:00Z",
                "predictor_source_sha256": "3" * 64,
                "label_source_sha256": "4" * 64,
                "permission": "Artificial unit test only; not empirical labels",
                "observed": {"status": "observed", "value": 2.0},
            }
            for name, value in [("candidate", 3.0), ("reference", 4.0)]:
                r[name] = {
                    "model": copy.deepcopy(models[name]),
                    "status": "available",
                    "value": value,
                    "lower": None,
                    "upper": None,
                    "reason": None,
                    "issue_at": issue,
                    "cycle_at": cycle,
                    "lead_days": 1,
                    "predictor_source_sha256": "3" * 64,
                }
            rows.append(r)
    manifest["observation_ids"] = [r["observation_id"] for r in rows]
    return rows, manifest


def unavailable(prediction):
    prediction.update(
        status="unsupported", value=None, lower=None, upper=None, reason="unit unsupported"
    )


def test_paired_metrics_cohorts_cluster_bootstrap_and_deterministic_hash():
    rows, manifest = inputs()
    report = evaluate(rows, manifest)
    assert report["row_weighted_common_support_metrics"]["candidate"] == {
        "mae": 1.0,
        "rmse": 1.0,
        "bias": 1.0,
    }
    assert report["row_weighted_common_support_metrics"]["reference"] == {
        "mae": 2.0,
        "rmse": 2.0,
        "bias": 2.0,
    }
    assert report["paired_farm_bootstrap"]["mae_difference_ci"] == [-1.0, -1.0]
    assert report["paired_farm_bootstrap"]["rmse_difference_ci"] == [-1.0, -1.0]
    assert report["paired_farm_bootstrap"]["clusters"] == 2
    assert all(report["gates"].values()) and report["eligible_for_release_review"]
    assert report["promotion"] == "not_performed" and report["serving_changed"] is False
    assert set(report["cohorts_on_common_support"]) == {
        "farm_group_id:F1",
        "farm_group_id:F2",
        "region_id:unit-region",
        "crop_id:Maize",
        "year:2025",
        "year:2026",
        "lead_days:1",
    }
    checksum = report.pop("report_sha256")
    assert checksum == canonical_sha(report)
    assert evaluate(rows, manifest)["report_sha256"] == checksum


def test_errors_compare_exact_common_support_and_count_abstentions():
    rows, manifest = inputs()
    unavailable(rows[0]["candidate"])
    rows[0]["reference"]["value"] = 100.0
    report = evaluate(rows, manifest)
    assert report["counts"]["common_support_pairs"] == 3
    assert report["model_support"]["candidate"]["coverage"] == 0.75
    assert report["model_support"]["candidate"]["abstention_statuses"] == {"unsupported": 1}
    assert report["row_weighted_common_support_metrics"]["reference"]["mae"] == 2.0
    assert report["model_support"]["reference"]["own_available_support_metrics"]["mae"] == 26.0
    assert (
        report["cohort_coverage_on_registered_rows"]["farm_group_id:F1"]["candidate_coverage"]
        == 0.5
    )
    assert (
        not report["gates"]["cohort_support_coverage"] and not report["eligible_for_release_review"]
    )


def test_whole_hard_farm_cannot_disappear_from_coverage_or_ids():
    rows, manifest = inputs()
    for row in rows:
        if row["farm_group_id"] == "F1":
            unavailable(row["candidate"])
    report = evaluate(rows, manifest)
    assert report["cohort_coverage_on_registered_rows"]["farm_group_id:F1"]["common_pairs"] == 0
    assert not report["gates"]["cohort_support_coverage"]
    with pytest.raises(ValueError, match="exactly cover predeclared"):
        evaluate(rows[1:], manifest)


def test_missing_labels_and_true_zero_are_distinct():
    rows, manifest = inputs()
    rows[0]["observed"]["value"] = 0.0
    rows[0]["candidate"]["value"] = 0.0
    rows[1]["observed"].update(status="missing", value=None)
    report = evaluate(rows, manifest)
    assert report["counts"]["eligible_observed_labels"] == 3
    assert report["counts"]["missing_labels"] == 1 and report["counts"]["label_coverage"] == 0.75
    assert not report["gates"]["label_coverage"]
    rows[1]["candidate"].update(status="unsupported", value=0.0, reason="missing")
    with pytest.raises(ValueError, match="Abstention needs null"):
        evaluate(rows, manifest)


@pytest.mark.parametrize("kind", ["empty", "synthetic", "missing", "abstaining"])
def test_no_empirical_common_pairs_produces_null_comparison(kind):
    rows, manifest = inputs()
    if kind == "empty":
        rows, manifest["observation_ids"] = [], []
    for row in rows:
        if kind == "synthetic":
            row["synthetic"] = True
        elif kind == "missing":
            row["observed"].update(status="missing", value=None)
        elif kind == "abstaining":
            unavailable(row["candidate"])
    report = evaluate(rows, manifest)
    assert report["status"] == "insufficient_data"
    assert report["row_weighted_common_support_metrics"] is None
    assert report["equal_farm_common_support_metrics"] is None
    assert report["paired_farm_bootstrap"] is None and not report["eligible_for_release_review"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, True])
def test_physical_values_must_be_finite_and_valid(bad):
    rows, manifest = inputs()
    rows[0]["candidate"]["value"] = bad
    with pytest.raises(ValueError):
        evaluate(rows, manifest)


@pytest.mark.parametrize(
    "change",
    [
        {"unit": "mm"},
        {"depth_bottom_cm": 100.0},
        {"lead_days": 2},
        {"harvest_basis": {"basis": "standard_moisture", "moisture_fraction": 0.14}},
        {"mode": "perfect_weather"},
    ],
)
def test_no_pooling_incompatible_units_depths_leads_bases_or_modes(change):
    rows, manifest = inputs()
    rows[0]["target"].update(change)
    with pytest.raises(ValueError):
        evaluate(rows, manifest)


def test_surface_vwc_and_root_reserve_contracts():
    rows, manifest = inputs()
    target = dict(
        task="surface_moisture",
        unit="m3/m3",
        depth_top_cm=0.0,
        depth_bottom_cm=5.0,
        harvest_basis=None,
        lead_days=1,
        mode="operational_forecast",
    )
    manifest["target"] = target
    for row in rows:
        row["target"] = copy.deepcopy(target)
        row["observed"]["value"] = 0.2
        row["candidate"]["value"], row["reference"]["value"] = 0.21, 0.23
    assert evaluate(rows, manifest)["target"]["unit"] == "m3/m3"
    rows[0]["candidate"]["value"] = 1.1
    with pytest.raises(ValueError):
        evaluate(rows, manifest)
    rows[0]["candidate"]["value"] = 0.21
    target.update(task="root_zone_reserve", unit="mm", depth_bottom_cm=100.0)
    for row in rows:
        row["target"] = copy.deepcopy(target)
    assert evaluate(rows, manifest)["target"]["unit"] == "mm"
    rows[0]["candidate"]["value"] = 1001.0
    with pytest.raises(ValueError):
        evaluate(rows, manifest)


@pytest.mark.parametrize("group", ["farm_group_ids", "geographic_group_ids", "grid_group_ids"])
def test_partition_group_leakage_is_rejected(group):
    rows, manifest = inputs()
    manifest["fit"][group].append(manifest["holdout"][group][0])
    with pytest.raises(ValueError, match="leakage"):
        evaluate(rows, manifest)


@pytest.mark.parametrize("field", ["selection_frozen_at", "gate_frozen_at"])
def test_premature_holdout_opening_rejected(field):
    rows, manifest = inputs()
    manifest[field] = manifest["holdout_opened_at"]
    with pytest.raises(ValueError, match="not_before_holdout"):
        evaluate(rows, manifest)


def test_future_year_leakage_rejected_but_explicit_diagnostic_never_promotes():
    rows, manifest = inputs()
    manifest["fit"]["years"] = [2025]
    with pytest.raises(ValueError, match="non_future_holdout"):
        evaluate(rows, manifest)
    manifest["purpose"] = "diagnostic_reuse"
    report = evaluate(rows, manifest)
    assert report["gates"]["mae_improvement"] and not report["eligible_for_release_review"]
    assert report["declared_lineage_violations"] == ["non_future_holdout_years"]
    assert report["promotion"] == "not_performed"


def test_cycle_and_availability_gates_allow_publication_after_initialization():
    rows, manifest = inputs()
    assert evaluate(rows, manifest)["counts"]["common_support_pairs"] == 4
    rows[0]["predictor_available_at"] = "2025-05-31T07:00:00Z"
    with pytest.raises(ValueError, match="unavailable at issue"):
        evaluate(rows, manifest)
    rows[0]["predictor_available_at"] = "2025-05-31T04:00:00Z"
    rows[0]["reference"]["cycle_at"] = "2025-05-30T00:00:00Z"
    with pytest.raises(ValueError, match="exact issue/cycle/lead"):
        evaluate(rows, manifest)


def test_oracle_and_historical_reconstruction_are_explicit_modes():
    rows, manifest = inputs()
    manifest["target"]["mode"] = "perfect_weather"
    for row in rows:
        row["target"]["mode"] = "perfect_weather"
        row["predictor_available_at"] = f"{row['year']}-06-02T00:00:00Z"
    oracle = evaluate(rows, manifest)
    assert oracle["target"]["mode"] == "perfect_weather"
    assert not oracle["eligible_for_release_review"]
    manifest["target"].update(mode="historical_reconstruction", lead_days=0)
    for row in rows:
        row["target"] = copy.deepcopy(manifest["target"])
        row["issue_at"] = f"{row['year']}-06-03T00:00:00Z"
        for name in ["candidate", "reference"]:
            row[name].update(issue_at=row["issue_at"], lead_days=0)
    historical = evaluate(rows, manifest)
    assert historical["target"]["mode"] == "historical_reconstruction"
    assert not historical["eligible_for_release_review"]


def test_duplicate_ids_or_changed_model_selection_are_rejected():
    rows, manifest = inputs()
    rows[1]["observation_id"] = rows[0]["observation_id"]
    with pytest.raises(ValueError, match="Duplicate observation_id"):
        evaluate(rows, manifest)
    rows, manifest = inputs()
    rows[0]["candidate"]["model"]["version"] = "different-after-holdout"
    with pytest.raises(ValueError, match="frozen selection"):
        evaluate(rows, manifest)


def test_equal_farm_metrics_do_not_turn_long_farms_into_independent_evidence():
    rows, manifest = inputs()
    rows = [rows[0], rows[1], copy.deepcopy(rows[1]), copy.deepcopy(rows[1])]
    for i, row in enumerate(rows):
        row["observation_id"] = f"cluster-pair-{i}"
        row["candidate"]["value"] = 2.0 if i == 0 else 4.0
        row["reference"]["value"] = 3.0
    manifest["observation_ids"] = [r["observation_id"] for r in rows]
    report = evaluate(rows, manifest)
    assert report["row_weighted_common_support_metrics"]["candidate"]["mae"] == 1.5
    assert report["equal_farm_common_support_metrics"]["candidate"]["mae"] == 1.0
    assert report["paired_farm_bootstrap"]["mae_difference_ci"] == [-1.0, 1.0]
    assert report["paired_farm_bootstrap"]["clusters"] == 2
    assert not report["gates"]["minimum_years"]


def test_per_farm_regression_and_interval_coverage_can_fail_aggregate_improvement():
    rows, manifest = inputs()
    for row in rows:
        row["candidate"].update(
            value=2.0 if row["farm_group_id"] == "F1" else 5.0, lower=2.0, upper=5.0
        )
    manifest["gates"].update(
        require_intervals=True, min_interval_coverage=1.0, max_mean_interval_width=2.0
    )
    report = evaluate(rows, manifest)
    assert report["gates"]["mae_improvement"]
    assert not report["gates"]["cohort_non_regression"]
    assert not report["gates"]["interval_width"]
    rows[0]["candidate"].update(value=3.0, lower=2.5, upper=3.5)
    report = evaluate(rows, manifest)
    assert not report["gates"]["interval_coverage"]
    rows[0]["candidate"].update(lower=None, upper=None)
    report = evaluate(rows, manifest)
    assert not report["gates"]["interval_support"]


def test_unknown_lineage_blocks_release_review():
    rows, manifest = inputs()
    manifest["lineage_known"] = False
    assert not evaluate(rows, manifest)["eligible_for_release_review"]


def test_files_cli_hashes_duplicate_json_keys_and_no_input_overwrite(tmp_path):
    rows, manifest = inputs()
    predictions, meta, output = [
        tmp_path / p for p in ["predictions.jsonl", "manifest.json", "report.json"]
    ]
    predictions.write_text("\n".join(json.dumps(r) for r in rows))
    meta.write_text(json.dumps(manifest))
    report = evaluate_files(predictions, meta)
    assert len(report["input_files_sha256"]["predictions"]) == 64
    command = [
        sys.executable,
        "-m",
        "field_shift_ml.accuracy_benchmark",
        "--predictions",
        str(predictions),
        "--manifest",
        str(meta),
        "--output",
        str(output),
    ]
    environment = {k: v for k, v in __import__("os").environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text()) == report
    result = subprocess.run(
        command[:-1] + [str(predictions)],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        cwd=tmp_path,
    )
    assert result.returncode != 0
    assert "Output must not overwrite" in result.stderr
    meta.write_text('{"schema_version":"a","schema_version":"b"}')
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        evaluate_files(predictions, meta)


def test_large_finite_yield_errors_do_not_overflow_the_metric_math():
    rows, manifest = inputs()
    for row in rows:
        row["candidate"]["value"] = 1e300
        row["reference"]["value"] = 2e300
    report = evaluate(rows, manifest)
    assert math.isfinite(report["row_weighted_common_support_metrics"]["candidate"]["rmse"])


def test_partial_intervals_cannot_claim_policy_coverage_on_all_pairs():
    rows, manifest = inputs()
    rows[0]["candidate"].update(lower=1.0, upper=4.0)
    manifest["gates"].update(min_interval_coverage=0.9, max_mean_interval_width=4.0)
    report = evaluate(rows, manifest)
    assert report["candidate_intervals_on_common_support"]["coverage"] == 1.0
    assert report["candidate_intervals_on_common_support"]["fraction_of_scored"] == 0.25
    assert not report["gates"]["interval_support"] and not report["eligible_for_release_review"]


def test_resource_and_json_bounds_are_rejected(tmp_path, monkeypatch):
    import field_shift_ml.accuracy_benchmark as module

    rows, manifest = inputs()
    manifest["gates"]["bootstrap_reps"] = 2001
    with pytest.raises(ValueError, match="bootstrap_reps"):
        evaluate(rows, manifest)
    predictions, meta = tmp_path / "p.jsonl", tmp_path / "m.json"
    predictions.write_text("x" * 100)
    meta.write_text("{}")
    monkeypatch.setattr(module, "MAX_BYTES", 50)
    with pytest.raises(ValueError, match="64 MiB"):
        evaluate_files(predictions, meta)
    monkeypatch.setattr(module, "MAX_BYTES", 1000)
    predictions.write_text('{"value": NaN}')
    with pytest.raises(ValueError, match="Invalid JSON NaN"):
        evaluate_files(predictions, meta)


def test_minimum_improvement_includes_boundary_but_not_a_tie():
    rows, manifest = inputs()
    manifest["gates"].update(min_relative_mae_improvement=0.5, min_relative_rmse_improvement=0.5)
    report = evaluate(rows, manifest)
    assert report["gates"]["mae_improvement"] and report["gates"]["rmse_improvement"]
    manifest["gates"].update(min_relative_mae_improvement=0.0, min_relative_rmse_improvement=0.0)
    for row in rows:
        row["candidate"]["value"] = row["reference"]["value"]
    report = evaluate(rows, manifest)
    assert not report["gates"]["mae_improvement"] and not report["gates"]["rmse_improvement"]


def test_partially_declared_training_lineage_cannot_look_independent():
    rows, manifest = inputs()
    manifest["fit"]["years"] = []
    with pytest.raises(ValueError, match="fit_years_missing"):
        evaluate(rows, manifest)
    manifest["purpose"] = "diagnostic_reuse"
    assert not evaluate(rows, manifest)["eligible_for_release_review"]


def test_perfect_weather_oracle_cannot_qualify_for_operational_release_review():
    rows, manifest = inputs()
    manifest["target"]["mode"] = "perfect_weather"
    for row in rows:
        row["target"]["mode"] = "perfect_weather"
        row["predictor_available_at"] = f"{row['year']}-06-02T00:00:00Z"
    report = evaluate(rows, manifest)
    assert report["gates"]["mae_improvement"] and report["gates"]["rmse_improvement"]
    assert report["row_weighted_common_support_metrics"]["candidate"]["mae"] == 1.0
    assert not report["gates"]["operational_forecast_evidence"]
    assert not report["eligible_for_release_review"]
    assert "operational_forecast_evidence" in report["failed_gates"]


def test_missing_label_region_is_retained_and_fails_its_predeclared_coverage_floor():
    rows, manifest = inputs()
    manifest["gates"]["min_label_coverage"] = 0.8
    missing = copy.deepcopy(rows[0])
    missing.update(
        observation_id="missing-region",
        region_id="fully-missing-region",
        observed={"status": "missing", "value": None},
    )
    rows.append(missing)
    manifest["observation_ids"].append("missing-region")
    report = evaluate(rows, manifest)
    assert report["counts"]["label_coverage"] == 0.8 and report["gates"]["label_coverage"]
    cohort = report["cohort_coverage_on_registered_rows"]["region_id:fully-missing-region"]
    assert cohort["registered_rows"] == 1 and cohort["missing_labels"] == 1
    assert cohort["eligible_labels"] == 0 and cohort["label_coverage"] == 0
    assert cohort["common_pairs"] == 0
    assert (
        not report["gates"]["cohort_label_coverage"] and not report["eligible_for_release_review"]
    )
    assert report["row_weighted_common_support_metrics"]["candidate"]["mae"] == 1.0
