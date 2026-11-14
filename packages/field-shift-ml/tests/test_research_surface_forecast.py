import json
from dataclasses import replace
from datetime import date, timedelta
from hashlib import sha256

import numpy as np
import pytest
from threadpoolctl import threadpool_limits

from field_shift.modules.geo_ml.ifs import IFSForecastDay, IFSForecastRun
from field_shift.modules.geo_ml.moisture_model import station_distance_km
from field_shift.modules.geo_ml.ssurgo import SoilProfile
from field_shift.modules.simulation.root_zone_inputs import root_zone_run_url
from field_shift_ml.research_surface_forecast import (
    BUFFER_KM,
    FEATURE_NAMES,
    FEATURE_SETS,
    RAW,
    Candidate,
    Design,
    Fold,
    cached_postcycle_samples,
    candidate_grid,
    choose_candidate,
    feature_groups,
    search,
    spatial_splits,
    station_weights,
)
from field_shift_ml.train_moisture import Station
from field_shift_ml.train_moisture_forecast import ForecastSample


def issued(station: Station, *, issue: date = date(2024, 6, 3)) -> IFSForecastRun:
    days = tuple(
        IFSForecastDay(
            date=issue + timedelta(days=lead),
            lead_days=lead,
            surface_moisture=0.33 - lead * 0.02,
            subsurface_moisture=0.35 - lead * 0.01,
            soil_temperature_c=18 + lead,
            air_temperature_c=21 + lead,
            precipitation_mm=6.0 if lead == 1 else 0.0,
            et0_mm=2.5,
        )
        for lead in range(1, 6)
    )
    return IFSForecastRun(issue, station.latitude, station.longitude, 0.33, days)


def test_trajectory_features_do_not_use_later_leads_or_probe_observations():
    station = Station("A", 41.2, -93.0, "", "")
    run = issued(station)
    row = ForecastSample(station, run, run.days[1], 0.42)
    modified = replace(
        run, days=(*run.days[:2], *(replace(day, precipitation_mm=99) for day in run.days[2:]))
    )
    soil = SoilProfile(latitude=41.2, longitude=-93.0, mukey="1")
    expected = feature_groups(row, soil)
    actual = feature_groups(replace(row, run=modified, observed=0.01), soil)
    for key, fields in FEATURE_NAMES.items():
        assert len(actual[key]) == len(fields)
        np.testing.assert_equal(
            np.asarray(actual[key], dtype=float), np.asarray(expected[key], dtype=float)
        )
    flux = dict(zip(FEATURE_NAMES["fluxes"], expected["fluxes"], strict=True))
    assert flux["recent_precipitation"] == pytest.approx(6 * np.exp(-0.7))
    assert flux["cumulative_water_balance"] == pytest.approx(1)
    assert flux["days_since_precipitation_in_run"] == 1


def test_soil_hydraulic_features_are_physical_and_reject_inverted_thresholds():
    station = Station("A", 41.2, -93.0, "", "")
    run = issued(station)
    row = ForecastSample(station, run, run.days[0], 0.42)
    soil = SoilProfile(
        latitude=41.2, longitude=-93.0, mukey="1", sand=30, field_capacity=0.3, wilting_point=0.1
    )
    values = dict(zip(FEATURE_NAMES["soil"], feature_groups(row, soil)["soil"], strict=True))
    assert values["available_water_capacity"] == pytest.approx(0.2)
    assert values["initial_relative_available_water"] == pytest.approx(1.15)
    assert values["precipitation_times_sand_fraction"] == pytest.approx(1.8)

    bad = soil.model_copy(update={"wilting_point": 0.5})
    values = dict(zip(FEATURE_NAMES["soil"], feature_groups(row, bad)["soil"], strict=True))
    assert np.isnan(values["available_water_capacity"])
    assert np.isnan(values["initial_relative_available_water"])


def design_fixture() -> Design:
    stations = (
        Station("boundary-south", 41.99, -93.0, "", ""),
        Station("boundary-north", 42.01, -93.0, "", ""),
        Station("east", 41.0, -85.0, "", ""),
        Station("west", 40.0, -97.0, "", ""),
        Station("north-east", 45.0, -87.0, "", ""),
        Station("north-west", 47.0, -99.0, "", ""),
    )
    rows = [
        ForecastSample(station, run, day, day.surface_moisture + 0.05)
        for station in stations
        for run in (issued(station),)
        for day in run.days
    ]
    return Design.from_samples(rows, {})


def test_spatial_splits_hold_all_days_at_each_site_and_exclude_boundary_neighbours():
    design = design_fixture()
    indices = np.arange(len(design.samples))
    seen = []
    for train, test in spatial_splits(design, indices, folds=3):
        assert not set(design.blocks[train]) & set(design.blocks[test])
        for train_site in {design.samples[index].station for index in train}:
            for test_site in {design.samples[index].station for index in test}:
                assert (
                    station_distance_km(
                        train_site.latitude,
                        train_site.longitude,
                        test_site.latitude,
                        test_site.longitude,
                    )
                    >= BUFFER_KM
                )
        seen.extend(test)
    assert sorted(seen) == indices.tolist()


def test_search_prefers_simple_model_within_tolerance_and_balances_sites():
    assert station_weights(np.asarray([0, 1, 1, 1])).tolist() == pytest.approx(
        [2, 2 / 3, 2 / 3, 2 / 3]
    )
    candidate = Candidate("ridge", "all", (40,))
    grid = (RAW, candidate)
    scores = {
        RAW: {"mae": 0.20, "rmse": 0.30},
        candidate: {"mae": 0.199, "rmse": 0.299},
    }
    assert choose_candidate(grid, scores) == RAW
    assert choose_candidate(grid, scores, allow_baseline=False) == candidate

    design = design_fixture()
    rows = np.arange(len(design.samples))
    with threadpool_limits(limits=1):
        fitted = search(design, rows, grid, folds=3)
    assert fitted[candidate]["mae"] < fitted[RAW]["mae"]


def test_missing_static_columns_keep_design_width_and_do_not_use_holdout_data():
    design = design_fixture()
    training = np.arange(25)
    fold = Fold.fit(design, training)
    for name, groups in FEATURE_SETS.items():
        width = sum(len(FEATURE_NAMES[group]) for group in groups)
        fitted, all_rows = fold.matrices[name]
        assert fitted.shape == (len(training), width)
        assert all_rows.shape == (len(design.samples), width)
        assert np.isfinite(all_rows).all()

    station = design.samples[0].station
    wrong_site = SoilProfile(latitude=30, longitude=-90, mukey="1")
    with pytest.raises(ValueError, match="coordinates"):
        Design.from_samples(design.samples, {station.id: wrong_site})


def test_candidate_grid_has_no_duplicate_identity_and_covers_feature_ablations():
    grid = candidate_grid()
    assert len({candidate.name for candidate in grid}) == len(grid)
    assert {candidate.features for candidate in grid if candidate.family == "ridge"} == set(
        FEATURE_SETS
    )
    assert {candidate.target for candidate in grid} == {"residual", "direct"}


def test_50r1_cache_reuse_records_missing_weeks_without_fabricating_predictions(tmp_path):
    root_cache = tmp_path / "root"
    root_cache.mkdir()
    site = Station("USCRN:1", 41.2, -93.0, "USCRN", "Test")
    forecast = issued(site, issue=date(2026, 5, 18))
    key = sha256(
        root_zone_run_url([(site.latitude, site.longitude)], forecast.initialized_on, None).encode()
    ).hexdigest()
    weather = {
        "soil_moisture_0_to_7cm_mean": ("m³/m³", "surface_moisture"),
        "soil_moisture_7_to_28cm_mean": ("m³/m³", "subsurface_moisture"),
        "soil_temperature_0_to_7cm_mean": ("°C", "soil_temperature_c"),
        "temperature_2m_mean": ("°C", "air_temperature_c"),
        "precipitation_sum": ("mm", "precipitation_mm"),
        "et0_fao_evapotranspiration": ("mm", "et0_mm"),
    }
    data = {
        "latitude": site.latitude,
        "longitude": site.longitude,
        "utc_offset_seconds": 0,
        "daily_units": {name: unit for name, (unit, _) in weather.items()},
        "daily": {
            "time": [
                (forecast.initialized_on + timedelta(days=lead)).isoformat() for lead in range(6)
            ],
            **{
                name: [0.33] + [getattr(day, attr) for day in forecast.days]
                for name, (_, attr) in weather.items()
            },
        },
    }
    payload = json.dumps(data).encode()
    (root_cache / f"{key}.json").write_bytes(payload)
    window = forecast.initialized_on, forecast.initialized_on + timedelta(days=7)
    rows, report = cached_postcycle_samples(
        {site: {forecast.days[0].date: 0.35}}, window, tmp_path, root_cache
    )
    assert len(rows) == 1
    assert rows[0].run.model_cycle == "50R1"
    assert rows[0].day.lead_days == 1
    assert rows[0].observed == 0.35
    assert report["weather_sources_sha256"] == {key: sha256(payload).hexdigest()}
    assert report["weather_issues"] == {site.id: {"available": 1, "expected": 2}}
