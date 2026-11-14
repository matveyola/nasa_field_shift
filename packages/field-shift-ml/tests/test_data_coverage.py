"""Published data coverage never means inference support or local validation."""

import csv
from pathlib import Path

import pytest

from field_shift_ml.data_coverage import APP_CROPS, summarize

SOURCE = Path(__file__).parents[1] / "benchmarks/metadata/cybench-paper-coverage.csv"


def write_rows(path, rows):
    with path.open("w", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def test_all_application_crops_have_explicit_status_and_no_claimed_field_validation():
    data = summarize(SOURCE)
    assert data["country_count"] == 43
    assert {r["crop_id"] for r in data["crops"]} == set(APP_CROPS)
    counts = {r["crop_id"]: r["published_country_count"] for r in data["crops"]}
    assert counts["Maize"] == 38 and counts["Wheat"] == 29
    assert counts["Rice"] == 0
    assert all(not r["validated_field_countries"] for r in data["crops"])
    assert all(r["exact_years"] is None and not r["raw_archive_verified"] for r in data["cells"])
    assert data["source"]["latest_inspected_archive"]["downloaded"] is False


def test_sparse_mexico_year_range_is_not_expanded():
    data = summarize(SOURCE)
    mexico = next(r for r in data["cells"] if r["country_code"] == "MX" and r["crop_id"] == "Maize")
    assert (mexico["min_year"], mexico["max_year"]) == (2014, 2022)
    assert mexico["exact_years"] is None


def test_source_alias_is_preserved_without_duplicate_greece():
    data = summarize(SOURCE)
    greece = [r for r in data["cells"] if r["country_code"] == "GR"]
    assert len(greece) == 2 and all(r["source_country_code"] == "EL" for r in greece)


@pytest.mark.parametrize("mutation", ["duplicate", "invalid_year", "fake_field", "complete_years"])
def test_false_coverage_claims_and_duplicates_are_rejected(tmp_path, mutation):
    with SOURCE.open(newline="") as source:
        rows = list(csv.DictReader(source))
    if mutation == "duplicate":
        rows.append(rows[0])
    elif mutation == "invalid_year":
        rows[0]["max_year"] = "1900"
    elif mutation == "fake_field":
        rows[0]["truth_level"] = "field"
    else:
        rows[0]["exact_years"] = "2003,2004,2005"
    path = tmp_path / "bad.csv"
    write_rows(path, rows)
    with pytest.raises(ValueError):
        summarize(path)
