"""Technical acceptance only: fixtures are synthetic, never accuracy observations."""

import builtins
import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from importlib.resources import files

import pytest

from field_shift_models.ruslan import (
    Era5Day,
    HistoricalRequest,
    HistoricalSurfaceAdapter,
    SmapDay,
    SoilFeatures,
)
from field_shift_models.ruslan.artifacts import ArtifactIntegrityError, load_artifacts
from field_shift_models.ruslan.features import prepare

AS_OF = datetime(2026, 11, 15, tzinfo=UTC)
SOURCE = hashlib.sha256(b"synthetic-identity-fixture-not-accuracy").hexdigest()


def snapshot():
    old_start = date(2024, 4, 1)
    target = date(2025, 7, 15)
    old = tuple(
        Era5Day(
            old_start + timedelta(days=i),
            AS_OF,
            SOURCE,
            0.2 + i * 0.001,
            0.3 + i * 0.0007,
            18,
            22,
            0,
        )
        for i in range(40)
    )
    current = tuple(
        Era5Day(target - timedelta(days=i), AS_OF, SOURCE, 0.24 + i * 0.002, 0.32, 19, 24, 0)
        for i in range(7)
    )
    smap = tuple(
        SmapDay(old_start + timedelta(days=i), AS_OF, SOURCE, 0.18 + i * 0.0011, 0)
        for i in range(40)
    ) + (SmapDay(target - timedelta(days=2), AS_OF, SOURCE, 0.22, 0),)
    return HistoricalRequest(
        target,
        AS_OF,
        41.704,
        -93.413,
        "US-IA",
        old + current,
        smap,
        SoilFeatures(AS_OF, SOURCE, 30, 25, 3),
    )


def test_actual_model_returns_research_identity_not_promotion():
    request = snapshot()
    output = HistoricalSurfaceAdapter().estimate(request)
    assert output.status == "available"
    assert output.mode == "experimental_l3_correction"
    assert output.product == "historical_surface_reconstruction"
    assert output.estimated_on == request.estimated_on
    assert output.available_at == AS_OF
    assert output.source_sha256 == (SOURCE,)
    assert output.promoted is False
    assert output.validation_status == "experimental_not_independently_reproduced"
    assert output.target_depth_cm == (0, 5)
    assert 0.01 <= output.value_m3_m3 <= 0.6
    assert output.research_interval_m3_m3[0] < output.research_interval_m3_m3[1]


def test_no_l3_fallback_never_imports_optional_dependency(monkeypatch):
    original_import = builtins.__import__

    def forbid(name, *args, **kwargs):
        if name == "lightgbm":
            raise AssertionError("Raw reference must not load the learned model")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", forbid)
    output = HistoricalSurfaceAdapter().estimate(replace(snapshot(), smap=(), soil=None))
    assert output.status == "available"
    assert output.mode == "raw_era5_fallback"
    assert output.value_m3_m3 == 0.24
    assert output.research_interval_m3_m3 is None


@pytest.mark.parametrize("age,flag", [(0, 0), (1, 0), (6, 0), (2, 1), (2, None), (2, -2)])
def test_unavailable_l3_never_uses_correction(age, flag):
    request = snapshot()
    latest = replace(
        request.smap[-1],
        day=request.estimated_on - timedelta(days=age),
        retrieval_quality_flag=flag,
    )
    output = HistoricalSurfaceAdapter().estimate(
        replace(request, smap=request.smap[:-1] + (latest,))
    )
    assert output.mode == "raw_era5_fallback"


@pytest.mark.parametrize(
    "lat,lon,region",
    [
        (50.4, 30.5, "UA-30"),
        (-22.9, -43.2, "BR-RJ"),
        (0.3, 32.6, "UG"),
        (-89.9, 0, "AQ"),
        (26.91, 70.91, "IN-RJ"),
        (10.05, 105.78, "VN"),
        (-16.5, -68.1, "BO"),
        (41.704, -93.413, "unknown"),
        (-22.9, -43.2, "US-IA"),
    ],
)
def test_unsupported_global_places_cannot_receive_midwest_model(lat, lon, region):
    output = HistoricalSurfaceAdapter().estimate(
        replace(snapshot(), latitude=lat, longitude=lon, region_code=region)
    )
    assert output.status == "out_of_domain"
    assert output.value_m3_m3 is None
    assert output.promoted is False


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("surface_vwc", None, "Latest three"),
        ("surface_vwc", float("nan"), "invalid input"),
        ("surface_vwc", float("inf"), "invalid input"),
        ("surface_vwc", -0.1, "invalid input"),
        ("soil_temperature_c", None, "snow evidence"),
        ("soil_temperature_c", 1, "Frozen"),
        ("snow_depth_m", 0.02, "Frozen"),
        ("source_sha256", "not-a-hash", "SHA256"),
        ("available_at", AS_OF + timedelta(days=1), "unavailable"),
    ],
)
def test_invalid_current_inputs_abstain(field, value, reason):
    request = snapshot()
    rows = list(request.era5)
    rows[-7] = replace(rows[-7], **{field: value})
    output = HistoricalSurfaceAdapter().estimate(replace(request, era5=tuple(rows)))
    assert output.value_m3_m3 is None
    assert reason in output.reason


def test_no_current_year_climatology_or_missing_climatology_substitution():
    request = snapshot()
    only_current = tuple(r for r in request.era5 if r.day.year == 2025)
    output = HistoricalSurfaceAdapter().estimate(replace(request, era5=only_current))
    assert output.status == "insufficient_data"
    assert "30 past-season" in output.reason
    # Extra current-year observations cannot influence past-only normalization.
    addition = replace(only_current[0], day=date(2025, 4, 1), surface_vwc=0.8)
    original = prepare(request)[0]
    updated = prepare(replace(request, era5=request.era5 + (addition,)))[0]
    assert original[:5] == updated[:5]


def test_future_data_and_duplicate_days_rejected_before_model():
    request = snapshot()
    for new in (request.era5[-1], replace(request.era5[-1], day=date(2025, 7, 16))):
        output = HistoricalSurfaceAdapter().estimate(replace(request, era5=request.era5 + (new,)))
        assert output.status == "insufficient_data"
        assert output.value_m3_m3 is None


def test_missing_day_is_not_interpolated():
    output = HistoricalSurfaceAdapter().estimate(replace(snapshot(), era5=snapshot().era5[:-1]))
    assert "Seven consecutive" in output.reason


def test_original_artifact_cannot_be_available_at_historical_issue():
    request = snapshot()
    old = datetime(2025, 7, 20, tzinfo=UTC)
    output = HistoricalSurfaceAdapter().estimate(replace(request, as_of=old))
    assert output.status == "unsupported"
    assert "artifact unavailable" in output.reason


def test_zero_surface_moisture_is_preserved_in_raw_reference():
    request = replace(snapshot(), smap=())
    rows = tuple(replace(r, surface_vwc=0.0) for r in request.era5)
    output = HistoricalSurfaceAdapter().estimate(replace(request, era5=rows))
    assert output.status == "available"
    assert output.value_m3_m3 == 0.0


def test_bad_soil_and_missing_soil_do_not_invent_trained_features():
    request = snapshot()
    for soil in (
        None,
        replace(request.soil, source="unknown"),
        replace(request.soil, sand_percent=80, clay_percent=40),
    ):
        output = HistoricalSurfaceAdapter().estimate(replace(request, soil=soil))
        assert output.value_m3_m3 is None


def test_pinned_receipt_records_known_limits():
    receipt, meta, _ = load_artifacts()
    assert len(receipt["frozen_mismatches"]) == 5
    assert receipt["frozen_task1_publish"] is False
    assert meta["validation"]["release"]["publish"] is True  # Upstream history, not our gate.
    assert receipt["imported_promotion"] is False
    assert receipt["empirical_reproduction"] == "blocked_missing_original_data"


@pytest.mark.parametrize("name", ["gbm.txt", "q10.txt", "q90.txt", "meta.json", "provenance.json"])
def test_tampered_assets_are_rejected(tmp_path, name):
    source = files("field_shift_models.ruslan").joinpath("assets")
    for item in source.iterdir():
        if item.is_file():
            (tmp_path / item.name).write_bytes(item.read_bytes())
    target = tmp_path / name
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(ArtifactIntegrityError, match="checksum mismatch"):
        HistoricalSurfaceAdapter(tmp_path)


def test_asset_byte_bound_and_absolute_path(tmp_path):
    with pytest.raises(ArtifactIntegrityError, match="absolute"):
        HistoricalSurfaceAdapter(type(tmp_path)("relative"))
    (tmp_path / "provenance.json").write_bytes(b" " * (65536 + 1))
    with pytest.raises(ArtifactIntegrityError, match="byte bound"):
        HistoricalSurfaceAdapter(tmp_path)


def test_missing_extra_reports_exact_workflow(monkeypatch):
    original_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "lightgbm":
            raise ImportError("not installed")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(ImportError, match=r"field-shift-models\[ruslan\]"):
        HistoricalSurfaceAdapter().estimate(snapshot())


def test_original_research_transform_and_booster_golden():
    """Golden from installed upstream468a03d on this synthetic snapshot; no accuracy claim.

    Original real.build -> robust.RobustData(('upto',2024)) -> Released's three
    exact Booster files. Original served point is0.2363 after four-digit rounding.
    The unrounded reference and feature vector also guard the conversion boundary.
    """
    import math

    expected = [
        1.7759071354792626,
        0.7858543770239267,
        1.9491663682089468,
        2.295684833668315,
        -0.5197776981890525,
        None,
        None,
        None,
        1.4608903486980178,
        2.0,
        -0.2280577712215198,
        -0.9736476020539838,
        19.0,
        24.0,
        30.0,
        25.0,
        3.0,
    ]
    features, level, amplitude = prepare(snapshot())
    for value, reference in zip(features, expected, strict=True):
        if reference is None:
            assert math.isnan(value)
        else:
            assert value == pytest.approx(reference, abs=1e-14, rel=0)
    assert level == 0.24
    assert amplitude == pytest.approx(0.011543396380615195, abs=1e-14, rel=0)
    result = HistoricalSurfaceAdapter().estimate(snapshot())
    assert (result.value_m3_m3, *result.research_interval_m3_m3) == pytest.approx(
        (0.23630367255242443, 0.11656678748776889, 0.35709654322515005), abs=1e-14, rel=0
    )


def test_request_without_utc_or_before_complete_day_is_rejected():
    request = snapshot()
    for instant in (AS_OF.replace(tzinfo=None), datetime(2025, 7, 15, tzinfo=UTC)):
        output = HistoricalSurfaceAdapter().estimate(replace(request, as_of=instant))
        assert output.value_m3_m3 is None
        assert output.status in {"insufficient_data", "unsupported"}


def test_naive_source_availability_rejected():
    request = snapshot()
    bad = replace(request.era5[0], available_at=AS_OF.replace(tzinfo=None))
    output = HistoricalSurfaceAdapter().estimate(replace(request, era5=(bad, *request.era5[1:])))
    assert output.status == "insufficient_data"
    assert "UTC" in output.reason


def test_missing_climatology_variance_is_not_imputed():
    request = snapshot()
    era5 = tuple(replace(r, surface_vwc=0.2) if r.day.year == 2024 else r for r in request.era5)
    output = HistoricalSurfaceAdapter().estimate(replace(request, era5=era5))
    assert output.status == "insufficient_data"
    assert "variance" in output.reason
