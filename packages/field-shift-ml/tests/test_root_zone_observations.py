import json
from datetime import date, datetime, timedelta

import pytest

from field_shift_ml.fetch_root_zone_observations import (
    DEPTHS_INCHES,
    DEPTHS_METRES,
    RootZoneSite,
    daily_profiles,
    integrate_probes_mm,
)


def test_probe_integration_handles_exact_one_metre_and_linear_interpolation():
    assert integrate_probes_mm((0.25,) * 5) == pytest.approx(250)
    # VWC increases with depth. The first segment is held constant from 0 to 2 inches.
    values = tuple(0.2 + depth * 0.1 for depth in DEPTHS_METRES)
    assert integrate_probes_mm(values) == pytest.approx(250 + 50 * DEPTHS_METRES[0] ** 2)


def test_daily_targets_require_concurrent_quality_controlled_profiles():
    target = date(2026, 9, 1)
    site = RootZoneSite("2077:TN:SCAN", "Eastview Farm", 35.13, -86.19, -6, "test")
    start = datetime(2026, 8, 31, 18)
    records = [
        {
            "stationElement": {
                "elementCode": "SMS",
                "heightDepth": depth,
                "durationName": "HOURLY",
                "storedUnitCode": "pct",
            },
            "values": [
                {
                    "date": (start + timedelta(hours=hour)).isoformat(),
                    "value": 25,
                    "qcFlag": "V",
                }
                for hour in range(24)
            ],
        }
        for depth in DEPTHS_INCHES
    ]
    station = [{"stationTriplet": site.station_id, "data": records}]
    assert daily_profiles(json.dumps(station), site, target, target) == {target: (250.0, 24)}
    for row in records[-1]["values"][:6]:
        row["qcFlag"] = "S"
    assert daily_profiles(json.dumps(station), site, target, target) == {}
