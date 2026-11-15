"""Tiny artificial source files test the public-data audit, not model accuracy."""

import csv
import json
import os
import subprocess
import sys
import zipfile

import pytest

from field_shift_ml.audit_public_crop_data import REQUIRED, audit, dry_yield_kg_ha


def csv_file(directory, name, rows):
    fields = sorted(REQUIRED[name])
    with (directory / name).open("w", encoding="cp1252", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def fixture(directory):
    for name in REQUIRED:
        csv_file(directory, name, [])
    csv_file(
        directory,
        "ardn-agro.csv",
        [
            dict(
                uniqueid="SITE",
                plotid="1",
                year="2020",
                crop="corn",
                grain_yield="845",
                grain_moisture="249",
            )
        ],
    )
    csv_file(
        directory,
        "ardn-ops_plant.csv",
        [
            dict(
                uniqueid="SITE",
                plotid="1",
                cropyear="2020",
                cashcrop="corn",
                operation="plant_corn",
                date="2020-05-01",
                planthybrid="unit-hybrid",
            )
        ],
    )
    csv_file(
        directory,
        "ardn-ops_harv.csv",
        [
            dict(
                uniqueid="SITE",
                plotid="1",
                cropyear="2020",
                cashcrop="corn",
                operation="harvest_corn",
                date="2020-05-02",
            )
        ],
    )
    csv_file(
        directory,
        "ardn-sitemeta.csv",
        [
            dict(uniqueid="description", state="State abbreviation"),
            dict(uniqueid="units", state="text"),
            dict(uniqueid="SITE", state="IA"),
            dict(uniqueid="UNKNOWN", state="NA"),
        ],
    )
    csv_file(
        directory,
        "ardn-soil.csv",
        [dict(uniqueid="SITE", lowerbound="120", SOIL02="0.45", SOIL31="0.3", SOIL32="0.1")],
    )
    csv_file(directory, "ardn-plotid.csv", [dict(uniqueid="SITE", plotid="1")])
    csv_file(
        directory,
        "ardn-weather_onsite.csv",
        [
            dict(
                station="SITE",
                day=day,
                highc="25",
                lowc="15",
                precipmm="0",
                srad_onsite="18",
                windspeed_onsite="2",
            )
            for day in ["2020/05/01", "2020/05/02"]
        ],
    )
    worksheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>SITE</t></is></c>'
        '<c r="B1"><v>1</v></c><c r="C1"><v>2020</v></c>'
        '<c r="S1"><v>1000</v></c></row></sheetData></worksheet>'
    )
    with zipfile.ZipFile(directory / "cscap.xlsx", "w") as workbook:
        workbook.writestr("xl/worksheets/sheet2.xml", worksheet)


@pytest.mark.parametrize("crop,expected", [("corn", 845.0), ("soybean", 870.0), ("wheat", 865.0)])
def test_source_standard_moisture_conversion(crop, expected):
    assert dry_yield_kg_ha(1000.0, crop) == pytest.approx(expected)
    with pytest.raises(ValueError):
        dry_yield_kg_ha(float("nan"), crop)


def test_annotation_exclusion_basis_calendar_and_wrapper_scope(tmp_path):
    fixture(tmp_path)
    report = audit(tmp_path)
    assert report["metadata_annotation_rows_excluded"] == 2
    assert report["known_state_codes"] == ["IA"] and report["unknown_state_rows"] == 1
    assert report["grain_records_by_crop"]["corn"] == {
        "grain_records": 1,
        "unambiguous_sow_harvest": 1,
        "same_year_ordered_calendar": 1,
        "calendar_with_named_hybrid": 1,
        "calendar_complete_temp_rain": 1,
        "calendar_complete_temp_rain_srad_wind": 1,
    }
    basis = report["dry_basis_crosscheck_against_parent_standard_moisture"]
    assert basis["matched_rows"] == {"corn": 1} and basis["differences_over_0_11kg_ha"] == 0
    assert report["soil_measured_max_depth_cm"] == 120
    assert report["soil_hydraulic_complete_rows"] == 1
    assert "NOT an intrinsic native-engine requirement" in report["readiness_scope"]
    assert "No engine validations executed" in report["readiness_count_method"]
    assert len(report["input_sha256"]) == 8 and report["yield_sites"] == 1


def test_basis_mismatch_and_missing_weather_remain_visible(tmp_path):
    fixture(tmp_path)
    csv_file(
        tmp_path,
        "ardn-agro.csv",
        [
            dict(
                uniqueid="SITE",
                plotid="1",
                year="2020",
                crop="corn",
                grain_yield="1000",
                grain_moisture="249",
            )
        ],
    )
    csv_file(tmp_path, "ardn-weather_onsite.csv", [])
    report = audit(tmp_path)
    assert (
        report["dry_basis_crosscheck_against_parent_standard_moisture"][
            "differences_over_0_11kg_ha"
        ]
        == 1
    )
    assert report["grain_records_by_crop"]["corn"]["calendar_complete_temp_rain"] == 0
    assert report["full_strict_engine_ready_records"] == 0


def test_required_files_and_headers_fail_cleanly(tmp_path):
    with pytest.raises(ValueError, match="Required input missing"):
        audit(tmp_path)
    fixture(tmp_path)
    (tmp_path / "ardn-agro.csv").write_text("wrong_header\n0\n")
    with pytest.raises(ValueError, match="missing columns"):
        audit(tmp_path)


def test_portable_cli_requires_explicit_paths_and_protects_sources(tmp_path):
    fixture(tmp_path)
    output = tmp_path / "report.json"
    command = [
        sys.executable,
        "-m",
        "field_shift_ml.audit_public_crop_data",
        "--data-dir",
        str(tmp_path),
        "--output",
        str(output),
    ]
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        command, env=env, cwd=tmp_path, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text())["yield_sites"] == 1
    result = subprocess.run(
        command[:-1] + [str(tmp_path / "ardn-agro.csv")],
        env=env,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0 and "must not overwrite" in result.stderr
