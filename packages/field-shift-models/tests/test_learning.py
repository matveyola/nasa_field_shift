"""Artificial labels in unit tests exercise barriers, not field accuracy evidence."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from field_shift.contracts.planning_v1.models import TrainingObservation

from field_shift_models.experiments import compare_frozen_embeddings
from field_shift_models.fixtures import repository_root
from field_shift_models.learning import (
    Gate,
    Row,
    calibrate_one_parameter,
    check_partitions,
    ingest_jsonl,
    residual_benchmark,
)


def observation(year=2020, group="fit", index=0, synthetic=True):
    raw = json.loads(
        (repository_root() / "contracts/planning-v1/examples/planning-contracts.json").read_text()
    )["observation"]
    raw.update(
        observation_id=f"{group}-{year}-{index}",
        farm_group_id=f"{group}-farm-{index % 3}",
        grid_group_id=f"{group}-grid-{index % 3}",
        geographic_group_id=f"{group}-geo-{index % 3}",
        observed_at=f"{year}-06-01T00:00:00Z",
        available_at=f"{year}-06-02T00:00:00Z",
        value=0.2 + (index % 4) * 0.01,
        synthetic=synthetic,
    )
    return TrainingObservation.model_validate_json(json.dumps(raw))


def row(year=2020, partition="fit", index=0, synthetic=True):
    o = observation(year, partition, index, synthetic)
    instant = o.observed_at - timedelta(days=5)
    return Row(
        o,
        instant,
        instant,
        instant,
        "ifs-pinned",
        "archived_forecast",
        5,
        {"fit": 10.0, "calibration": 20.0, "test": 30.0}[partition],
        index % 3,
        (float(index % 4), None),
        0.18,
        partition,
    )


def gate():
    # Artificial unit-test protocol; these values are not production scientific thresholds.
    return Gate(datetime(2010, 1, 1, tzinfo=UTC), 3, 2, 60, 20, 0.01, 0.02, 0.8, 0.5, 10.0, 20.0)


def rows(synthetic=True):
    return [
        row(y, p, i, synthetic)
        for p, years in [
            ("fit", [2020, 2021]),
            ("calibration", [2022, 2023]),
            ("test", [2024, 2025]),
        ]
        for y in years
        for i in range(12)
    ]


def test_ingestion_preserves_provenance_and_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "observations.jsonl"
    o = observation()
    path.write_text(o.model_dump_json() + "\n")
    assert ingest_jsonl(path) == [o]
    path.write_text((o.model_dump_json() + "\n") * 2)
    with pytest.raises(ValueError, match="Duplicate"):
        ingest_jsonl(path)


def test_no_real_labels_means_no_accuracy_or_promotion():
    result = residual_benchmark(rows(), gate(), holdout_opened_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert result["training_status"] == "insufficient_training_data"
    assert result["metrics"] is None and result["promotion"] == "insufficient_evidence"
    assert (
        calibrate_one_parameter(
            [row()], [1.0, 2.0], lambda *_: [], parameter_name="WP", bounds=(0.1, 3.0)
        )["metrics"]
        is None
    )
    assert (
        compare_frozen_embeddings([], {}, {}, gate(), holdout_opened_at=datetime.now(UTC))[
            "metrics"
        ]
        is None
    )


@pytest.mark.parametrize("attribute", ["farm_group_id", "grid_group_id", "geographic_group_id"])
def test_cross_group_leakage(attribute):
    data = rows(False)
    a, b = data[0], next(r for r in data if r.partition == "test")
    changed = replace(
        b,
        observation=b.observation.model_copy(update={attribute: getattr(a.observation, attribute)}),
    )
    data[data.index(b)] = changed
    with pytest.raises(ValueError, match="overlaps partitions"):
        check_partitions(data, 10.0)


def test_future_year_and_spatial_buffer_required():
    data = rows(False)
    fit = data[0]
    test = next(r for r in data if r.partition == "test")
    data[data.index(test)] = replace(test, latitude=fit.latitude, longitude=fit.longitude)
    with pytest.raises(ValueError, match="buffer"):
        check_partitions(data, 10.0)
    data = rows(False)
    data.append(row(2020, "test", 99, False))
    with pytest.raises(ValueError, match="later years"):
        check_partitions(data, 10.0)


def test_issue_time_and_forecast_oracle_separation():
    r = row()
    with pytest.raises(ValueError, match="unavailable"):
        replace(r, predictor_available_at=r.issue_at + timedelta(days=1))
    with pytest.raises(ValueError, match="Reanalysis"):
        replace(r, forcing_kind="reanalysis")
    with pytest.raises(ValueError, match="chronology/lead"):
        replace(r, lead_days=4)
    data = rows(False)
    data[0] = replace(data[0], forcing_kind="perfect_weather")
    with pytest.raises(ValueError, match="Separate"):
        residual_benchmark(data, gate(), holdout_opened_at=datetime(2026, 1, 1, tzinfo=UTC))


def test_moisture_depths_never_share_a_target():
    data = rows(False)
    data[0] = replace(
        data[0], observation=data[0].observation.model_copy(update={"depth_bottom_cm": 25.0})
    )
    with pytest.raises(ValueError, match="Separate"):
        residual_benchmark(data, gate(), holdout_opened_at=datetime(2026, 1, 1, tzinfo=UTC))


def test_protocol_must_precede_holdout():
    with pytest.raises(ValueError, match="Freeze protocol"):
        residual_benchmark([], gate(), holdout_opened_at=datetime(2009, 1, 1, tzinfo=UTC))


def test_fit_transforms_only_see_training_folds(monkeypatch):
    from sklearn.preprocessing import StandardScaler

    seen = []
    original = StandardScaler.fit

    def record(self, x, *args, **kwargs):
        seen.append(x.copy())
        return original(self, x, *args, **kwargs)

    monkeypatch.setattr(StandardScaler, "fit", record)
    data = rows(False)
    # Shift both predictors and holdout outcomes: a saturated tree can ignore an
    # extreme predictor alone, which does not imply failed interval coverage.
    data = [
        replace(
            r, features=(1.0e6, None), observation=r.observation.model_copy(update={"value": 0.8})
        )
        if r.partition == "test"
        else r
        for r in data
    ]
    result = residual_benchmark(data, gate(), holdout_opened_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert result["training_status"] == "trained" and result["serving_changed"] is False
    assert len(seen) == 1 and np.max(seen[0]) < 1.0e6
    assert set(result["learners"]) == {"ridge", "shallow_boosting"}
    for report in result["learners"].values():
        assert "interval_coverage" in report["failed_gates"]
        assert report["promotion"] == "failed"
        assert any(k.startswith("farm:") for k in report["cohorts"])


def test_calibration_needs_identifiable_bounded_search():
    observations = [row(y, "fit", i, False) for y in [2020, 2021] for i in range(3)]
    result = calibrate_one_parameter(
        observations,
        [0.1, 0.2, 0.3],
        lambda parameter, obs: [0.1 if parameter is None else parameter for _ in obs],
        parameter_name="WP",
        bounds=(0.05, 0.5),
    )
    assert result["training_status"] == "trained"
    assert result["parameter"] == 0.2 and result["promotion"] == "candidate"
    assert result["independent_validation"] is False
    with pytest.raises(ValueError, match="2–21"):
        calibrate_one_parameter(
            observations,
            [float("nan"), 1.0],
            lambda *_: [],
            parameter_name="WP",
            bounds=(0.05, 2.0),
        )


def test_calibration_cannot_open_holdout_or_leave_physical_bounds():
    data = [row(2020, "test", 0, False)]
    with pytest.raises(ValueError, match="fit trials only"):
        calibrate_one_parameter(
            data, [0.1, 0.2], lambda *_: [], parameter_name="WP", bounds=(0.05, 0.3)
        )
    data = [row(y, "fit", i, False) for y in [2020, 2021] for i in range(3)]
    with pytest.raises(ValueError, match="2–21"):
        calibrate_one_parameter(
            data, [0.1, 0.4], lambda *_: [], parameter_name="WP", bounds=(0.05, 0.3)
        )


def test_frozen_training_manifest_empty_report_and_tamper(tmp_path):
    from field_shift_models.training_cli import train_manifest

    g = gate()
    protocol = {**vars(g), "frozen_at": g.frozen_at.isoformat()}
    raw = {
        "gate": protocol,
        "gate_sha256": g.checksum,
        "holdout_opened_at": "2026-01-01T00:00:00+00:00",
        "rows": [],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw))
    assert train_manifest(path)["report"]["training_status"] == "insufficient_training_data"
    raw["gate"]["min_rows"] += 1
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="checksum"):
        train_manifest(path)


def test_frozen_EO_manifest_cannot_leak_future_imagery():
    from field_shift_models.provenance import digest

    data = [row()]
    key = data[0].observation.observation_id
    embeddings = {key: [1.0, 2.0]}
    manifest = {
        "checkpoint": "ibm-nasa-geospatial/Prithvi-EO-2.0-tiny-TL",
        "checkpoint_sha256": "a" * 64,
        "extractor_version": "synthetic-unit-fixture",
        "bands": ["blue", "green", "red", "nir_narrow", "swir1", "swir2"],
        "reflectance_scale": "surface_reflectance_0_1",
        "temporal_policy": "before_issue",
        "geolocation_policy": "WGS84_TL",
        "pooling": "field_aggregate",
        "label_support": "field_aggregate_not_pixel_ground_truth",
        "permission": "artificial unit fixture only",
        "features_sha256": digest(embeddings),
        "available_at_by_observation": {key: "2028-01-01T00:00:00+00:00"},
    }
    with pytest.raises(ValueError, match="unavailable"):
        compare_frozen_embeddings(
            data, embeddings, manifest, gate(), holdout_opened_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
    manifest["features_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="checksum"):
        compare_frozen_embeddings(
            data, embeddings, manifest, gate(), holdout_opened_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
