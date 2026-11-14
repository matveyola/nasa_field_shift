import json
from datetime import date, datetime, timedelta

import pytest

from field_shift_ml.fetch_observations import (
    Station,
    daily_observations,
    fetch_ifc,
    ifc_readings,
    scan_readings,
    uscrn_readings,
)


def test_uscrn_hour_ending_timestamps_and_missing_record_padding():
    row = ["0"] * 38
    row[:3] = ["54902", "20250802", "0000"]
    row[28], row[33] = ".325", "17"
    raw = "\x00" * 244 + " ".join(row)
    assert list(uscrn_readings(raw)) == [("54902", datetime(2025, 8, 1, 23), 0.325, 17)]


def test_uscrn_rejects_malformed_records():
    with pytest.raises(ValueError, match="hourly record format"):
        list(uscrn_readings("54902 20250802 0000"))


def test_scan_uses_station_standard_time_and_only_valid_two_inch_values():
    station = "2031:IA:SCAN"
    element = {
        "elementCode": "SMS",
        "heightDepth": -2,
        "durationName": "HOURLY",
        "storedUnitCode": "pct",
    }
    series = {
        "stationElement": element,
        "values": [
            {"date": "2025-08-01 19:00", "qcFlag": "V", "value": 25.4},
            {"date": "2025-08-01 20:00", "qcFlag": "S", "value": 99.9},
            {"date": "2025-08-01 21:00", "qcFlag": "V", "value": None},
        ],
    }
    response = [{"stationTriplet": station, "data": [series]}]
    assert list(scan_readings(json.dumps(response), station, -6)) == [
        (datetime(2025, 8, 2, 1), pytest.approx(0.254))
    ]

    element["heightDepth"] = -4
    with pytest.raises(ValueError, match="sensor definition"):
        list(scan_readings(json.dumps(response), station, -6))


def test_daily_observations_reject_incomplete_days_and_do_not_count_duplicate_hours():
    start = datetime(2025, 8, 1)
    station = Station("USCRN:54902", 41.56, -93.29, "IA", "USCRN", "Des Moines")
    readings = [(start + timedelta(hours=hour), 0.25) for hour in range(20)]
    readings.extend((start + timedelta(hours=hour), 0.5) for hour in range(24, 43))
    readings.extend([readings[-1], (start + timedelta(hours=47), float("nan"))])
    results = daily_observations(station, readings, date(2025, 8, 1), date(2025, 8, 2))
    assert [(row.date, row.samples, row.moisture) for row in results] == [
        (date(2025, 8, 1), 20, 0.25)
    ]
    with pytest.raises(ValueError, match="Conflicting readings"):
        daily_observations(
            station,
            [*readings, (start, 0.5)],
            date(2025, 8, 1),
            date(2025, 8, 2),
        )


def test_ifc_aggregates_qualified_quarter_hours_at_the_explicit_utc_offset():
    raw = """time,data.sm_surf0
2025-08-01 19:00-05,0.2
2025-08-01 19:15-05,0.3
2025-08-01 19:15-05,0.3
2025-08-01 19:30-05,0.4
2025-08-01 19:31-05,0.8
2025-08-01 19:45-05,NA
2025-08-01 20:00-05,0.5
2025-08-01 20:15-05,0.5
2025-08-01 20:30-05,1.0
"""
    day = date(2025, 8, 2)
    assert list(ifc_readings(raw, day, day)) == [(datetime(2025, 8, 2), pytest.approx(0.3))]

    conflict = raw.replace(
        "19:15-05,0.3\n2025-08-01 19:15-05,0.3", "19:15-05,0.3\n2025-08-01 19:15-05,0.4"
    )
    with pytest.raises(ValueError, match="Conflicting Iowa Flood Center measurements"):
        list(ifc_readings(conflict, day, day))


def test_ifc_checks_the_sealed_coordinates_before_loading_observations():
    class Catalog:
        def read(self, url):
            assert url.endswith("/list_hydrostations.csv")
            return "name,id,long,lat\nCCO02,5080,-91.9433,41.7418\n"

    day = date(2025, 8, 2)
    with pytest.raises(ValueError, match="coordinates have changed"):
        list(fetch_ifc(Catalog(), day, day, locations={"CCO02": (41.741627, -91.9433)}))
