import json
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from field_shift_science.geo_ml.era5_land import UNIT, MoistureSourceError
from field_shift_science.geo_ml.moisture_features import (
    FEATURE_NAMES,
    MoistureFeatures,
    fetch_moisture_features,
)
from field_shift_science.geo_ml.moisture_model import (
    SOIL_ATTRIBUTES,
    V2_FEATURE_NAMES,
    MoistureModel,
    MoistureModelUnavailable,
    MoistureModelV2,
    get_moisture_model,
    get_moisture_model_v2,
    station_distance_km,
)
from field_shift_science.geo_ml.moisture_model import artifact_version as version_for
from field_shift_science.geo_ml.ssurgo import SoilProfile


def _series(day: date) -> dict:
    daily = {
        "soil_moisture_0_to_7cm_mean": [0.25, 0.25, 0.25, 0.25, 0.4, 0.4, 0.4],
        "soil_moisture_7_to_28cm_mean": [0.2] * 7,
        "soil_moisture_28_to_100cm_mean": [0.3] * 7,
        "soil_temperature_0_to_7cm_mean": [3] * 7,
        "temperature_2m_mean": [4] * 7,
        "snow_depth_mean": [0] * 7,
    }
    return {
        "latitude": 42.0,
        "longitude": -93.7,
        "utc_offset_seconds": 0,
        "daily_units": dict(zip(daily, [UNIT, UNIT, UNIT, "°C", "°C", "m"], strict=True)),
        "daily": {
            "time": [(day + timedelta(days=offset)).isoformat() for offset in range(-6, 1)],
            **daily,
        },
    }


def _artifact() -> dict:
    return {
        "artifact_schema": 1,
        "feature_names": list(FEATURE_NAMES),
        "promoted": True,
        "fit": {"kind": "linear", "intercept": -0.08, "coefficients": [0.0] * len(FEATURE_NAMES)},
        "coverage": {
            "bounds": [36.0, -102.0, 50.0, -80.0],
            "station_locations": [[42.0, -93.7]],
            "max_station_distance_km": 180.0,
            "validated_months": [4, 5, 6, 7, 8, 9, 10, 11],
        },
        "interval_level": 0.9,
        "interval_half_width": 0.1,
        "validation": {},
    }


def _load(artifact: dict) -> MoistureModel:
    payload = {"version": version_for(artifact), **artifact}
    return MoistureModel.from_json(json.dumps(payload).encode())


def test_shared_predictors_use_only_current_and_previous_utc_days():
    day = date(2026, 8, 1)

    def fetch(url):
        params = parse_qs(urlparse(url).query)
        assert params["start_date"] == ["2026-07-26"]
        assert params["end_date"] == [day.isoformat()]
        assert params["timezone"] == ["GMT"]
        assert params["models"] == ["era5_land"]
        return _series(day)

    frame = fetch_moisture_features(41.99, -93.72, day, day, fetch=fetch)
    (sample,) = frame.days
    assert sample.values[:5] == pytest.approx((0.4, 0.2, 0.3, 0.4, 2.2 / 7))
    assert not sample.is_frozen
    prediction = _load(_artifact()).predict(sample)
    assert (prediction.value, prediction.lower, prediction.upper) == pytest.approx(
        (0.32, 0.22, 0.42)
    )


def test_features_reject_bad_units_or_incomplete_antecedent_series():
    day = date(2026, 8, 1)
    wrong_unit = _series(day)
    wrong_unit["daily_units"]["temperature_2m_mean"] = "°F"
    with pytest.raises(MoistureSourceError, match="invalid feature series"):
        fetch_moisture_features(42, -93.7, day, day, fetch=lambda _: wrong_unit)

    missing = _series(day)
    missing["daily"]["soil_moisture_0_to_7cm_mean"][0] = None
    with pytest.raises(MoistureSourceError, match="no complete feature day"):
        fetch_moisture_features(42, -93.7, day, day, fetch=lambda _: missing)


def test_model_rejects_snowy_days_and_constrains_supported_region_and_months():
    day = date(2026, 11, 5)
    data = _series(day)
    data["daily"]["snow_depth_mean"][-1] = 0.01
    (sample,) = fetch_moisture_features(42, -93.7, day, day, fetch=lambda _: data).days
    model = _load(_artifact())
    assert model.supports_location(41.71, -93.41)
    assert not model.supports_location(44.50, -90.0)
    assert not model.supports_location(50, 30)
    assert model.supports_date(day)
    assert not model.supports_date(date(2026, 1, 1))
    with pytest.raises(ValueError, match="frozen or snowy"):
        model.predict(sample)


def test_model_refuses_tampered_artifact_and_nonfinite_feature_values():
    artifact = _artifact()
    version = version_for(artifact)
    artifact["promoted"] = False
    with pytest.raises(MoistureModelUnavailable):
        MoistureModel.from_json(json.dumps({"version": version, **artifact}).encode())

    model = _load(_artifact())
    sample = MoistureFeatures(date(2026, 8, 1), (float("nan"),) * len(FEATURE_NAMES), False)
    with pytest.raises(ValueError, match="finite feature vector"):
        model.predict(sample)


def test_portable_boosted_trees_apply_the_correct_terminal_node():
    artifact = _artifact()
    artifact["fit"] = {
        "kind": "trees",
        "intercept": 0.01,
        "learning_rate": 0.5,
        "trees": [
            {
                "nodes": [
                    [0, 0.3, 1, 2, 0.0],
                    [-1, -2.0, -1, -1, 0.1],
                    [-1, -2.0, -1, -1, -0.1],
                ],
            }
        ],
    }
    model = _load(artifact)
    day = date(2026, 8, 1)
    assert model.predict(
        MoistureFeatures(day, (0.2,) * len(FEATURE_NAMES), False)
    ).value == pytest.approx(0.26)
    assert model.predict(
        MoistureFeatures(day, (0.5,) * len(FEATURE_NAMES), False)
    ).value == pytest.approx(0.46)


def test_bundled_artifact_promotion_and_unseen_location_evidence():
    model = get_moisture_model()
    validation = model.validation
    assert model.promoted is all(validation["promotion_checks"].values())
    assert model.supports_location(41.705785, -93.410507)
    assert model.supports_date(date(2026, 11, 1))
    splits = validation["station_splits"]
    fitting = splits["train"] + splits["calibration"]
    testing = splits["holdout"] + splits["independent_farms"]
    for fit in fitting:
        assert fit["to"] <= validation["fit_end"]
        for held_out in testing + splits["independent_farms_spatial_only"]:
            assert fit["station_id"] != held_out["station_id"]
            assert fit["block"] != held_out["block"]
            assert (
                station_distance_km(
                    fit["latitude"], fit["longitude"], held_out["latitude"], held_out["longitude"]
                )
                >= validation["spatial_buffer_km"]
            )
    assert all(site["from"] >= validation["evaluation_start"] for site in testing)


def test_v2_uses_soil_features_and_rejects_an_unverified_promotion():
    artifact = _artifact()
    artifact.update(
        {
            "artifact_schema": 2,
            "feature_names": list(V2_FEATURE_NAMES),
            "soil_medians": [35.0, 40.0, 25.0, 2.0, 1.3, 0.3, 0.15],
            "promoted": False,
        }
    )
    coefficients = [0.0] * len(V2_FEATURE_NAMES)
    coefficients[V2_FEATURE_NAMES.index("clay")] = 0.002
    artifact["fit"]["intercept"] = 0.0
    artifact["fit"]["coefficients"] = coefficients
    encoded = json.dumps({"version": version_for(artifact), **artifact}).encode()
    model = MoistureModelV2.from_json(encoded)
    day = MoistureFeatures(date(2026, 8, 1), (0.2,) * len(FEATURE_NAMES), False)
    soil = SoilProfile(latitude=42, longitude=-93.7, sand=25, clay=30, mukey="123")
    assert model.predict(day, soil).value == pytest.approx(0.26)
    assert model.predict(day, soil.model_copy(update={"clay": 15})).value == pytest.approx(0.23)
    with pytest.raises(ValueError, match="sand and clay"):
        model.predict(day, soil.model_copy(update={"clay": None}))
    assert len(artifact["soil_medians"]) == len(SOIL_ATTRIBUTES)

    artifact["promoted"] = True
    encoded = json.dumps({"version": version_for(artifact), **artifact}).encode()
    with pytest.raises(MoistureModelUnavailable):
        MoistureModelV2.from_json(encoded)


def test_bundled_v2_preserves_separate_farm_validation():
    model = get_moisture_model_v2()
    validation = model.validation
    splits = validation["station_splits"]
    report = validation["independent_farms"]
    assert model.promoted is all(report["promotion_checks"].values())
    assert report["summary"]["site_count"] >= 3
    assert {"model", "era5_land", "v1"} <= report["summary"].keys()
    for farm in report["matched_stations"]:
        assert date.fromisoformat(farm["from"]).year == validation["evaluation_year"]
        for site in splits["train"] + splits["calibration"]:
            assert site["to"] <= validation["fit_end"]
            assert site["station_id"] != farm["station_id"] and site["block"] != farm["block"]
            assert (
                station_distance_km(
                    site["latitude"], site["longitude"], farm["latitude"], farm["longitude"]
                )
                >= 40
            )
