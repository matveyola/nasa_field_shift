from datetime import date
from email.message import Message
from urllib.error import HTTPError

import numpy as np
import pytest
from field_shift_science.geo_ml.era5_land import MoistureSourceError

from field_shift_ml import train_moisture
from field_shift_ml.historical_surface_features import (
    ERA5_COLUMNS,
    StationRows,
    parse_weather,
    trailing,
)
from field_shift_ml.search_historical_surface import (
    RAW,
    Dataset,
    Recipe,
    SupportRule,
    cross_validate,
    design,
    recipe_grid,
)
from field_shift_ml.train_moisture import Station, separate_from


def test_weather_features_use_only_current_and_previous_days():
    rain = np.array([1.0, 2.0, 4.0, 99.0])
    sums = trailing(rain, 3)
    assert np.isnan(sums[:2]).all()
    assert sums[2:].tolist() == [7.0, 105.0]
    rain[-1] = 0
    assert trailing(rain, 3)[2] == 7.0


def test_weather_archive_requires_utc_matching_coordinates_and_verified_units():
    station = Station("site", 42.02, -92.95, "test", "Example")
    day = date(2024, 6, 1)
    response = {
        "latitude": 42.0,
        "longitude": -93.0,
        "utc_offset_seconds": 0,
        "daily_units": {name: units for name, (units, _, _) in ERA5_COLUMNS.items()},
        "daily": {
            "time": [day.isoformat()],
            **{name: [1.0] for name in ERA5_COLUMNS},
        },
    }
    parse_weather(response, station, (day,))
    response["daily_units"]["rain_sum"] = "cm"
    with pytest.raises(ValueError, match="rain_sum"):
        parse_weather(response, station, (day,))
    response["daily_units"]["rain_sum"] = "mm"
    response["daily"]["temperature_2m_min"] = [2]
    with pytest.raises(ValueError, match="temperature range"):
        parse_weather(response, station, (day,))


def test_imputation_and_scaling_are_fitted_on_training_rows_only():
    features = np.array([[1.0], [3.0], [np.nan], [101.0]])
    training, held = design(features, np.array([0, 1]), np.array([2, 3]), np.ones(2))
    assert training[:, 0].tolist() == [-1.0, 1.0]
    assert held[:, 0].tolist() == [0.0, 99.0]


def test_daily_archive_quota_exits_without_retrying(monkeypatch):
    headers = Message()
    headers["Retry-After"] = "31841"
    calls = []

    def rate_limited(*args, **kwargs):
        calls.append(1)
        raise HTTPError("https://archive-api.open-meteo.com/", 429, "quota", headers, None)

    monkeypatch.setattr(train_moisture, "urlopen", rate_limited)
    with pytest.raises(MoistureSourceError, match="offline retry window"):
        train_moisture.download_features("https://archive-api.open-meteo.com/")
    assert calls == [1]


def test_distance_taper_returns_to_the_archive_at_and_beyond_the_radius():
    rule = SupportRule(0.5, 180)
    raw = np.full(4, 0.4)
    learned = np.full(4, 0.2)
    corrected = rule.predict(raw, learned, np.array([0, 90, 180, 240]))
    assert corrected.tolist() == pytest.approx([0.3, 0.35, 0.4, 0.4])


def test_candidate_grid_and_buffered_cross_validation_have_no_shared_sites():
    assert len(set(recipe_grid())) == len(recipe_grid())
    locations = (
        (41.9, -93.0),
        (42.1, -93.0),
        (43.0, -91.0),
        (40.5, -96.0),
        (39.0, -99.0),
        (46.0, -90.0),
    )
    dataset = Dataset(
        [
            StationRows(
                Station(str(i), lat, lon, "test", "Test"),
                (date(2024, 5, 1), date(2024, 5, 2)),
                np.array([0.2, 0.3]),
                {"raw": {"surface": np.array([0.1, 0.2])}},
                {},
            )
            for i, (lat, lon) in enumerate(locations)
        ]
    )
    folds = dataset.splits(np.arange(len(dataset.actual)), 3)
    for training, held in folds:
        train_sites = {dataset.stations[i] for i in dataset.site[training]}
        hold_sites = {dataset.stations[i] for i in dataset.site[held]}
        assert all(separate_from(station, hold_sites) for station in train_sites)
    ridge = Recipe("ridge", ("raw",), (30,))
    scores = cross_validate(dataset, folds, (RAW, ridge))
    assert scores[RAW]["mae"] == pytest.approx(0.1)
    assert scores[ridge]["mae"] == pytest.approx(0.0, abs=1e-12)
