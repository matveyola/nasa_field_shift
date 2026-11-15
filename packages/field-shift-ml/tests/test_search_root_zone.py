from dataclasses import replace
from datetime import date, timedelta
from urllib.error import HTTPError

import numpy as np
import pytest
from field_shift_science.simulation.root_zone_inputs import RootZoneRun, RootZoneWeatherDay

from field_shift_ml.fetch_root_zone_observations import RootZoneSite
from field_shift_ml.root_zone_features import FEATURES, sample_features
from field_shift_ml.root_zone_history import LOOKBACK, archive_station, history_features
from field_shift_ml.root_zone_soil import COLUMNS, parse_root_profile
from field_shift_ml.search_root_zone import (
    Fit,
    Fold,
    Search,
    buffered_folds,
    fit_grid,
    ifs_join_sha256,
    imputed_scaled,
)
from field_shift_ml.train_root_zone import Sample


def example(station: str, latitude: float, longitude: float) -> Sample:
    issue = date(2026, 6, 1)
    days = tuple(
        RootZoneWeatherDay(
            issue + timedelta(days=lead),
            lead,
            float(lead),
            3.5,
            22.0,
            19.0,
            (0.3, 0.35, 0.4),
        )
        for lead in range(1, 6)
    )
    site = RootZoneSite(station, station, latitude, longitude, -6, "fit")
    run = RootZoneRun(issue, latitude, longitude, "50R1", (0.3, 0.35, 0.4), days)
    return Sample(site, run, days[1], 100, 90, 120)


def test_forecast_features_only_use_the_requested_leads_and_ignore_the_observation():
    original = example("fit", 40.5, -92.3)
    changed = replace(
        original,
        actual=0,
        run=replace(
            original.run,
            days=original.run.days[:2]
            + tuple(replace(day, rain_mm=100) for day in original.run.days[2:]),
        ),
    )
    soil = {"available_capacity_mm": 200}
    features = sample_features(original, soil)
    assert sample_features(changed, soil) == features
    assert all(len(features[group]) == len(names) for group, names in FEATURES.items())
    assert features["weather"][4:7] == (3, 7, -4)
    assert features["soil"][-1] == pytest.approx(-0.02)


def test_history_observes_the_issue_date_archive_publication_lag():
    issue = date(2026, 6, 1)
    latest = issue - timedelta(days=6)
    history = {
        (latest - timedelta(days=offset)).isoformat(): [300 - offset, 2, 3]
        for offset in range(LOOKBACK)
    }
    for offset in range(1, 7):
        history[(latest + timedelta(days=offset)).isoformat()] = [800, 100, 0]
    output = history_features(history, issue, 360)
    assert output == pytest.approx((300, 297, 286.5, 6, 60, -7, -28, 0))
    del history[latest.isoformat()]
    assert history_features(history, issue, 360) == (None,) * len(output)


def test_history_quota_error_stops_without_retries_or_caching(tmp_path, monkeypatch):
    attempts = 0

    def quota_error(request, *, timeout):
        nonlocal attempts
        attempts += 1
        raise HTTPError(request.full_url, 429, "daily quota", {"Retry-After": "30000"}, None)

    monkeypatch.setattr("field_shift_ml.root_zone_history.urlopen", quota_error)
    station = example("fit", 40.5, -92.3).site
    with pytest.raises(RuntimeError, match="HTTP 429"):
        archive_station(station, date(2026, 5, 1), date(2026, 8, 1), tmp_path)
    assert attempts == 1
    assert not list(tmp_path.iterdir())


def test_every_optional_feature_subset_is_covered_by_the_common_ridge():
    fits = fit_grid(include_soil=True, include_history=True)
    bundles = {fit.groups for fit in fits if fit.family == "ridge" and fit.parameters == (100,)}
    assert len(bundles) == 2 ** (len(FEATURES) - 1)
    assert ("core",) in bundles
    assert tuple(FEATURES) in bundles
    assert len(fits) == len(set(fits))
    assert all(
        "soil" not in fit.groups for fit in fit_grid(include_soil=False, include_history=True)
    )


def test_outer_and_inner_geographic_folds_are_disjoint_and_buffered():
    samples = [
        example("held", 41.9, -95),
        example("same-block", 40.1, -95),
        example("near", 42.01, -95),
        *(example(f"site-{index}", 42, -90 + 3 * index) for index in range(5)),
    ]
    outer = buffered_folds(samples, tuple(range(len(samples))), 40)
    held = next(fold for fold in outer if fold.test == (0,))
    assert held.train == tuple(range(3, 8))
    for inner in buffered_folds(samples, held.train, 40):
        assert set(inner.train + inner.test) <= set(held.train)
        assert set(inner.train).isdisjoint(inner.test)


def test_preprocessing_uses_train_only_imputation_and_handles_constant_columns():
    train = np.array([[1.0, np.nan, 1.0], [3.0, np.nan, 1.0]])
    test = np.array([[np.nan, 20.0, 1.0]])
    fit, held = imputed_scaled(train, test, np.array([1, 1]))
    np.testing.assert_allclose(fit, [[-1, 0, 0], [1, 0, 0]])
    np.testing.assert_allclose(held, [[0, 0, 0]])


def test_shared_training_context_reuses_predictions_without_using_held_labels(monkeypatch):
    monkeypatch.setattr(
        "field_shift_ml.search_root_zone.fit_grid",
        lambda _soil, _history: (Fit("bias", ()), Fit("ridge", ("core",), (100,))),
    )
    samples = [example(str(index), 40 + index, -94) for index in range(5)]
    targets = {
        row.site.station_id: {row.day.date: 380 + index} for index, row in enumerate(samples)
    }
    fit = (0, 1, 2)
    first, second = Fold(fit, (3,)), Fold(fit, (4,))
    search = Search(samples, targets, {}, {}, fit)
    search.validation_by_training[fit].update((3, 4))
    left, right = search.corrections(first), search.corrections(second)
    assert len(search.prediction_cache) == 1

    targets["3"][samples[3].day.date] = 80
    targets["4"][samples[4].day.date] = 150
    repeated = Search(samples, targets, {}, {}, fit)
    np.testing.assert_allclose(left, repeated.corrections(first))
    np.testing.assert_allclose(right, repeated.corrections(second))


def test_rank_rejects_regression_on_one_station_despite_pooled_improvement():
    samples = [replace(example(str(index), 40 + index, -94), actual=120) for index in range(5)]
    targets = {row.site.station_id: {row.day.date: 270} for row in samples}
    indices = np.arange(len(samples))
    search = Search(samples, targets, {}, {}, tuple(indices))
    storage = np.array([[200, 200, 200, 200, 250], [265, 265, 265, 265, 230], [265] * 5])
    ranked = search.rank(indices, storage)
    assert ranked["objective"][1] < ranked["objective"][0]
    assert ranked["eligible"].tolist() == [False, False, True]


def test_stress_gate_rejects_better_regression_that_misses_a_stress_case():
    samples = [
        replace(example("dry", 40, -94), actual=60, baseline=55),
        *(replace(example(str(index), 41 + index, -94), actual=120) for index in range(4)),
    ]
    targets = {row.site.station_id: {row.day.date: row.actual + 150} for row in samples}
    indices = np.arange(len(samples))
    storage = np.array([[160, *([200] * 4)], [220, *([265] * 4)], [200, *([265] * 4)]])
    search = Search(samples, targets, {}, {}, tuple(indices))
    assert search.rank(indices, storage)["eligible"].tolist() == [False, True, True]

    search = Search(samples, targets, {}, {}, tuple(indices), stress_gate=True)
    assert search.rank(indices, storage)["eligible"].tolist() == [False, False, True]


def test_ifs_join_digest_covers_run_values_and_is_independent_of_pair_order():
    first, second = example("first", 40, -94), example("second", 41, -94)
    changed = replace(first, run=replace(first.run, initial_layers=(0.1, 0.2, 0.3)))
    assert ifs_join_sha256([first, second]) == ifs_join_sha256([second, first])
    assert ifs_join_sha256([first, second]) != ifs_join_sha256([changed, second])


def test_soil_profile_integrates_transitional_horizons_without_double_counting():
    component = ["500", "20", "3.0", "Well drained"]
    top = [0, 30, 40, 20, 4, 1.3, 0.2, 12]
    transition = [0, 30, 60, 40, 4, 1.3, 0.2, 12]
    bottom = [30, 120, 30, 45, 2, 1.5, 0.1, 4]
    profile = parse_root_profile(
        {"Table": [list(COLUMNS), component + top, component + transition, component + bottom]}
    )
    assert profile is not None
    assert profile["sand_pct"] == 36
    assert profile["clay_pct"] == 40.5
    assert profile["subsoil_clay_difference_pct"] == 15
    assert profile["available_capacity_mm"] == pytest.approx(130)
    assert profile["root_profile_coverage"] == 1
    assert profile["drainage_rank"] == 4


def test_shallow_or_absent_soil_data_are_not_treated_as_zero_storage():
    component = ["500", "20", None, None]
    shallow = [0, 60, 40, 20, 4, 1.3, 0.2, 12]
    profile = parse_root_profile({"Table": [list(COLUMNS), component + shallow]})
    assert profile is not None
    assert profile["available_capacity_mm"] is None
    assert profile["root_profile_coverage"] == 0.6
    assert parse_root_profile({"Table": []}) is None
    with pytest.raises(ValueError, match="Unexpected SSURGO"):
        parse_root_profile({"Error": "Bad query"})
