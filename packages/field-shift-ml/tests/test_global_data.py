from __future__ import annotations

import csv
import json
from datetime import date, timedelta

import pytest

from field_shift_ml import global_data as data


def document():
    return {
        "header": {
            "time_standard": "UTC",
            "start": "19810101",
            "end": "20151231",
            "fill_value": -999,
        },
        "parameters": {k: {"units": v} for k, v in data.PARAMETERS.items()},
        "properties": {
            "parameter": {k: {"20110101": 1, "20110102": -999} for k in data.PARAMETERS}
        },
        "geometry": {"coordinates": [-90.0, 40.0, 200]},
    }


def write_csv(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_power_fill_values_and_units_are_not_faked():
    doc = document()
    values = data.validate_power(doc)
    assert values["T2M"] == {date(2011, 1, 1): 1.0}
    doc["parameters"]["T2M"]["units"] = "K"
    with pytest.raises(ValueError, match="units"):
        data.validate_power(doc)


@pytest.mark.parametrize("field,value", [("time_standard", "LST"), ("end", "20161231")])
def test_wrong_power_time_basis_rejected(field, value):
    doc = document()
    doc["header"][field] = value
    with pytest.raises(ValueError):
        data.validate_power(doc)


def test_preseason_features_ignore_future_and_recent_unavailable_weather():
    cutoff = date(2011, 3, 1)
    first = date(1984, 1, 1)
    daily = {
        k: {first + timedelta(days=n): 2.0 for n in range((cutoff - first).days + 30)}
        for k in data.PARAMETERS
    }
    baseline = data.weather_features(daily, cutoff)
    for values in daily.values():
        for day in values:
            if day > cutoff - timedelta(days=7):
                values[day] = 1e9
    assert data.weather_features(daily, cutoff) == baseline
    assert all(value == 2 for value in baseline.values())
    for day in list(daily["T2M"]):
        if day.year <= 2009 and day.month == 1:
            del daily["T2M"][day]
    with pytest.raises(ValueError, match="coverage"):
        data.weather_features(daily, cutoff)


def test_calendar_uses_prior_year_for_winter_wheat_and_fixed_dates():
    assert data.issue_date(2011, "wheat", "standard") == date(2010, 8, 1)
    assert data.issue_date(2015, "maize", "early") == date(2015, 2, 1)
    with pytest.raises(ValueError):
        data.issue_date(2015, "unsupported", "early")
    with pytest.raises(ValueError, match="climatology"):
        data.weather_features({}, date(2009, 10, 1))


def test_csv_annotation_and_malformed_rows(tmp_path):
    path = tmp_path / "source.csv"
    path.write_text("uniqueid,value\ndescription,explanation\nunits,kg\nA,1\n")
    assert data.read_csv(path, {"uniqueid", "value"}) == [{"uniqueid": "A", "value": "1"}]
    path.write_text("uniqueid,value\nA\n")
    with pytest.raises(ValueError, match="Malformed"):
        data.read_csv(path, {"uniqueid", "value"})


def test_physical_aliases_grouped_and_irregular_coordinate_explicit(tmp_path):
    rows = []
    for name, longitude in [("A.one", -90.0), ("A.two", -90.01), ("B", -90.015)]:
        row = dict(uniqueid=name, state="IL")
        for corner in ("NW", "SW", "NE", "SE"):
            row[corner + ".Lat"] = "40" if corner == "NW" else "NA"
            row[corner + ".Lon"] = str(longitude) if corner == "NW" else "NA"
        rows.append(row)
    write_csv(tmp_path / "ardn-sitemeta.csv", rows)
    sites = data.sites(tmp_path)
    assert {s["physical_site"] for s in sites.values()} == {"A"}
    assert sites["A.one"]["coordinate_pairs"] == 1


def test_budget_stops_before_network_request(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "sites", lambda _: {"A": dict(latitude=40, longitude=-90)})
    monkeypatch.setattr(data, "urlopen", lambda *args, **kwargs: pytest.fail("network forbidden"))
    with pytest.raises(ValueError, match="budget"):
        data.acquire_power(tmp_path, tmp_path / "cache", budget_bytes=1)


def test_cache_coordinate_mismatch_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "sites", lambda _: {"A": dict(latitude=41, longitude=-90)})
    (tmp_path / "A-power-1981-2015.json").write_text(json.dumps(document()))
    with pytest.raises(ValueError, match="location"):
        data.acquire_power(tmp_path, tmp_path)


def test_panel_dry_yield_and_missing_or_ambiguous_sowing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        data,
        "sites",
        lambda _: {"A": dict(latitude=40, longitude=-90, state="IL", physical_site="A")},
    )
    monkeypatch.setattr(data, "weather_features", lambda *args: {"recent_T2M_30": 2.0})
    (tmp_path / "ardn-sitemeta.csv").write_text("fixture metadata")
    (tmp_path / "A-power-1981-2015.json").write_text(json.dumps(document()))
    agro = [
        dict(uniqueid="A", plotid=str(n), year="2011", crop="corn", grain_yield="10000")
        for n in range(3)
    ]
    planting = [
        dict(
            uniqueid="A",
            plotid=str(n),
            cropyear="2011",
            cashcrop="corn",
            operation="plant_corn",
            date="2011-05-01",
        )
        for n in (0, 1)
    ]
    planting.append(dict(planting[1], date="2011-05-02"))
    write_csv(tmp_path / "ardn-agro.csv", agro)
    write_csv(tmp_path / "ardn-ops_plant.csv", planting)
    write_csv(
        tmp_path / "ardn-ops_harv.csv",
        [dict(r, operation="harvest_corn", date="2011-10-01") for r in planting],
    )
    rows, audit = data.build_panel(tmp_path, tmp_path)
    assert len(rows) == 2  # One observed label, two issue schedules.
    assert {r["yield_t_ha"] for r in rows} == {10}
    assert audit["exclusions"]["ambiguous_or_missing_sowing"] == 2
    assert "sowing_date" not in rows[0]["features"]
    write_csv(tmp_path / "ardn-agro.csv", agro + [agro[0]])
    with pytest.raises(ValueError, match="Duplicate yield"):
        data.build_panel(tmp_path, tmp_path)


def test_global_download_records_real_response_and_resumes_without_network(tmp_path, monkeypatch):
    from io import BytesIO

    doc = document()
    doc["header"].update(start="20230101", end="20241231")
    doc["properties"]["parameter"] = {k: {"20230101": 2.0} for k in data.PARAMETERS}
    raw = json.dumps(doc).encode()
    monkeypatch.setattr(data, "GLOBAL_POINTS", {"point": (40.0, -90.0)})
    calls = []

    def fetch(request, timeout):
        calls.append(request.full_url)
        return BytesIO(raw)

    monkeypatch.setattr(data, "urlopen", fetch)
    first = data.acquire_global_panel(tmp_path)
    second = data.acquire_global_panel(tmp_path)
    assert len(calls) == 1
    assert first["points"][0]["sha256"] == second["points"][0]["sha256"]
    assert first["points"][0]["acquired_at"] is not None
    assert first["total_bytes"] == len(raw)
    assert (tmp_path / "global-point-power-2023-2024.json").read_bytes() == raw


def test_power_day_outside_declared_period_rejected():
    doc = document()
    doc["properties"]["parameter"]["T2M"]["20250101"] = 10.0
    with pytest.raises(ValueError, match="outside"):
        data.validate_power(doc)
