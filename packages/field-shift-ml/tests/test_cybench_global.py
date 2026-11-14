import csv
import io
import json
import zipfile
from datetime import date, timedelta

import numpy as np
import pytest

from field_shift_ml import cybench_global
from field_shift_ml.cybench_global import (
    FEATURES,
    PROTOCOL,
    WEATHER,
    aggregate_weather,
    canonical_hash,
    check_protocol,
    evaluate_panel,
    fetch_member,
    file_digest,
    freeze_protocol,
    is_spatial_holdout,
    label_available,
    make_weather_features,
    read_labels,
    region_group,
    scores,
    verify_member,
    weather_window,
    write_json,
)


def write_weather(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["adm_id", "date", *WEATHER])
        writer.writerows(rows)


def days(start, end):
    while start <= end:
        yield start
        start += timedelta(days=1)


def constant_weather(start, end, adm="CN011"):
    return [[adm, day.strftime("%Y%m%d"), 0, 20, 10, 2, 3, 1, 5] for day in days(start, end)]


def test_protocol_cannot_be_replaced_or_silently_changed(tmp_path):
    path = tmp_path / "protocol.json"
    frozen = freeze_protocol(path)
    assert frozen == check_protocol(path) == canonical_hash(PROTOCOL)
    altered = json.loads(path.read_text())
    altered["protocol"]["weather_lag_days"] = 0
    path.write_text(json.dumps(altered))
    with pytest.raises(ValueError, match="frozen"):
        check_protocol(path)
    with pytest.raises(ValueError, match="replace"):
        freeze_protocol(path)


def test_label_availability_includes_exact_issue_date_but_excludes_later_labels():
    # 2018 issue is 2016-12-31. 2015 labels are assumed released that exact date.
    assert label_available(2015, 2018)
    assert not label_available(2016, 2018)
    assert not label_available(2018, 2018)


def test_weather_window_has_ninety_days_and_fourteen_day_publication_lag():
    start, end = weather_window(2018)
    assert end == date(2016, 12, 17)
    assert start == date(2016, 9, 19)
    assert (end - start).days + 1 == 90


def test_streamed_features_include_leap_year_and_never_future_weather(tmp_path):
    path = tmp_path / "weather.csv"
    rows = constant_weather(date(2001, 1, 1), date(2005, 12, 31))
    rows += constant_weather(*weather_window(2008))
    # These later observations may be present in the archive but cannot enter 2008 features.
    rows += [["CN011", "20070101", 0, 60, 30, 999, 999, 999, 999]]
    write_weather(path, rows)
    annual, recent, _ = aggregate_weather(path, {"CN011"})
    features = make_weather_features("CN011", 2008, annual, recent)
    assert features is not None
    assert features[:7] == [0, 20, 10, 180, 270, 90, 5]
    assert features[7:10] == [0, 20, 10]
    assert features[10] == pytest.approx(2 * (365 * 5 + 1) / 5)
    rows[-1][5] = 1234567
    write_weather(path, rows)
    a2, r2, _ = aggregate_weather(path, {"CN011"})
    assert make_weather_features("CN011", 2008, a2, r2) == features


@pytest.mark.parametrize("kind", ["gap", "nan", "negative_rain", "inverted_temperature"])
def test_incomplete_or_invalid_weather_abstains_instead_of_imputing(tmp_path, kind):
    path = tmp_path / "weather.csv"
    rows = constant_weather(date(2001, 1, 1), date(2005, 12, 31))
    rows += constant_weather(*weather_window(2008))
    if kind == "gap":
        rows.pop()
    elif kind == "nan":
        rows[-1][4] = "nan"
    elif kind == "negative_rain":
        rows[-1][5] = -1
    else:
        rows[-1][2], rows[-1][3] = 30, 20
    write_weather(path, rows)
    annual, recent, counts = aggregate_weather(path, {"CN011"})
    assert make_weather_features("CN011", 2008, annual, recent) is None
    if kind != "gap":
        assert counts["invalid_weather_rows"] == 1


@pytest.mark.parametrize("unordered", [True, False])
def test_duplicate_and_unordered_weather_fail(tmp_path, unordered):
    path = tmp_path / "weather.csv"
    rows = constant_weather(date(2001, 1, 1), date(2001, 1, 2))
    rows = list(reversed(rows)) if unordered else [rows[0], rows[0]]
    write_weather(path, rows)
    with pytest.raises(ValueError, match="ordered|duplicate"):
        aggregate_weather(path, {"CN011"})


def test_zero_yield_is_real_eligible_observation_and_exclusions_are_counted(tmp_path):
    path = tmp_path / "yield.csv"
    path.write_text(
        "adm_id,harvest_year,yield\nCN011,2018,0\nCN012,2018,-1\n"
        "CN013,2018,nan\nCN014,2018,\nCN015,2018,4\n"
    )
    labels, counts = read_labels(path)
    assert [r["yield"] for r in labels] == [0, 4]
    assert counts["zero_yield"] == 1
    assert counts["negative_or_nonfinite_label"] == 2
    assert counts["missing_or_invalid_label"] == 1
    assert counts["source_rows"] == 5


def test_duplicate_yield_rows_cannot_be_averaged_away(tmp_path):
    path = tmp_path / "yield.csv"
    path.write_text("adm_id,harvest_year,yield\nCN011,2018,1\nCN011,2018,2\n")
    with pytest.raises(ValueError, match="duplicate"):
        read_labels(path)


@pytest.mark.parametrize(
    ("country", "adm", "expected"),
    [
        ("US", "US-21-007", "US-21"),
        ("BR", "BR1100015", "BR11"),
        ("FR", "FRK23", "FRK2"),
        ("CN", "CN061", "CN061"),
    ],
)
def test_spatial_group_uses_parent_area(country, adm, expected):
    assert region_group(country, adm) == expected


def test_actual_error_metrics_are_defined_for_zero_yields():
    result = scores(np.array([0.0, 2.0]), np.array([1.0, 1.0]))
    assert result["mae_t_ha"] == result["rmse_t_ha"] == 1
    assert result["bias_t_ha"] == 0
    assert result["nrmse_percent"] == 100
    assert result["r2"] == 0
    zero = scores(np.zeros(2), np.ones(2))
    assert zero["nrmse_percent"] is None and zero["r2"] is None


def test_identical_noninteger_targets_have_undefined_r2_without_epsilon_threshold():
    constant = scores(np.full(3, 0.1), np.ones(3))
    assert constant["r2"] is None
    small_real_variation = np.array([0.1, 0.1, 0.1 + 1e-10])
    assert scores(small_real_variation, small_real_variation)["r2"] == 1


def test_tampered_source_member_is_rejected(tmp_path):
    path = tmp_path / "yield.csv"
    path.write_text("original")
    sha, crc, size = file_digest(path)
    write_json(path.with_suffix(".manifest.json"), {"sha256": sha, "crc32": crc, "size": size})
    path.write_text("tampered")
    with pytest.raises(ValueError, match="checksum"):
        verify_member(path)


def test_full_download_response_is_closed_before_reading(monkeypatch):
    class Response:
        status = 200
        headers = {}
        closed = False

        def close(self):
            self.closed = True

        def read(self, *_):
            pytest.fail("must not consume an unbounded full-archive response")

    response = Response()
    monkeypatch.setattr(cybench_global.urllib.request, "urlopen", lambda *a, **kw: response)
    with pytest.raises(ValueError, match="bounded"):
        cybench_global.open_range(0, 99)
    assert response.closed


@pytest.mark.parametrize("corrupt_crc", [False, True])
def test_partial_zip_acquisition_requires_complete_member_crc(tmp_path, monkeypatch, corrupt_crc):
    raw = io.BytesIO()
    name = "cybench-data/maize/CN/yield_maize_CN.csv"
    payload = b"adm_id,harvest_year,yield\nCN011,2018,0\n"
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr(name, payload, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("padding", b"0" * 4096)
    content = raw.getvalue()
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        info = archive.getinfo(name)
    monkeypatch.setattr(cybench_global, "ARCHIVE_SIZE", len(content))
    monkeypatch.setattr(
        cybench_global, "open_range", lambda start, end: io.BytesIO(content[start : end + 1])
    )
    if corrupt_crc:
        info.CRC ^= 1
        with pytest.raises(ValueError, match="CRC"):
            fetch_member(info, tmp_path)
        assert not (tmp_path / "maize/CN/yield_maize_CN.csv").exists()
    else:
        manifest = fetch_member(info, tmp_path)
        path = tmp_path / "maize/CN/yield_maize_CN.csv"
        assert path.read_bytes() == payload
        assert manifest["crc_verified"]
        assert manifest["archive_md5_verified"] is False
        assert verify_member(path)["sha256"] == manifest["sha256"]


def test_fits_use_past_labels_and_disjoint_spatial_groups(tmp_path):
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    groups = [f"FR{i:03}" for i in range(30)]
    assert any(is_spatial_holdout("FR", group) for group in groups)
    path = prepared / "maize_FR.csv"
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["adm_id", "year", "group", "yield", *FEATURES])
        for year in range(2008, 2021):
            for index, group in enumerate(groups):
                # Huge held-out outcomes expose leakage; they must never reach training.
                value = 1000 if year >= 2016 else 2.0 + index / 30
                writer.writerow(
                    [group, year, group, value, year, *([float(index)] * (len(FEATURES) - 1))]
                )
    protocol_hash = canonical_hash(PROTOCOL)
    write_json(
        path.with_suffix(".json"),
        {"protocol_sha256": protocol_hash, "features_sha256": file_digest(path)[0]},
    )
    result = evaluate_panel(prepared, tmp_path / "predictions", "maize", "FR", protocol_hash)
    for fold in result["folds"]:
        assert max(fold["train_target_years"]) <= fold["harvest_year"] - 3
        if fold["track"] == "spatial_chronological":
            assert set(fold["train_groups"]).isdisjoint(fold["test_groups"])
    with (tmp_path / "predictions" / "maize_FR.csv").open() as stream:
        predictions = list(csv.DictReader(stream))
    initial = [float(r["predicted_yield_t_ha"]) for r in predictions if r["harvest_year"] == "2018"]
    assert max(initial) < 10


def test_prepared_feature_tampering_blocks_evaluation(tmp_path):
    path = tmp_path / "maize_FR.csv"
    path.write_text("bad")
    write_json(
        path.with_suffix(".json"),
        {"protocol_sha256": canonical_hash(PROTOCOL), "features_sha256": "wrong"},
    )
    with pytest.raises(ValueError, match="checksum"):
        evaluate_panel(tmp_path, tmp_path / "out", "maize", "FR", canonical_hash(PROTOCOL))


@pytest.mark.parametrize("country", ["CN", "BR"])
def test_source_rights_exclude_panels_before_reading_or_fitting(tmp_path, country):
    with pytest.raises(ValueError, match="source-rights"):
        evaluate_panel(tmp_path, tmp_path / "out", "maize", country, canonical_hash(PROTOCOL))
    protocol = tmp_path / "protocol.json"
    freeze_protocol(protocol)
    with pytest.raises(SystemExit):
        cybench_global.main(
            ["evaluate", "--protocol", str(protocol), "--panel", f"maize_{country}"]
        )
    assert not cybench_global.SOURCE_RIGHTS[country]["fit_eligible"]


def test_default_training_panels_have_open_source_terms():
    assert cybench_global.TRAINING_PANELS == (("maize", "FR"), ("maize", "US"), ("wheat", "FR"))
    assert all(
        cybench_global.SOURCE_RIGHTS[n]["fit_eligible"] for _, n in cybench_global.TRAINING_PANELS
    )
    assert (
        PROTOCOL["revision_reason"] == "source-rights filter before any fitting or error inspection"
    )
