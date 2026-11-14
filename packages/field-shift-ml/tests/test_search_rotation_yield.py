import json
from dataclasses import replace

import numpy as np
import pytest

from field_shift.modules.geo_ml.ssurgo import SoilProfile
from field_shift_ml.rotation_external import KBS_COLUMNS, kbs_contrasts
from field_shift_ml.rotation_features import (
    CLIMATE_COLUMNS,
    CLIMATE_FEATURES,
    CLIMATE_YEARS,
    SOIL_AGGREGATE_COLUMNS,
    climate_profile,
    feature_groups,
    location_of,
    parse_soil_aggregates,
    soil_values,
)
from field_shift_ml.search_rotation_yield import (
    REFERENCE,
    Candidate,
    feature_matrices,
    predict_fold,
    search_space,
    validate,
)
from field_shift_ml.train_rotation_yield import Sample


def climate_export(days=365, newline="\n"):
    rows = [
        "Latitude: 43.29925 Longitude: -89.35245",
        "Daymet Software Version 4.0",
        ",".join(CLIMATE_COLUMNS),
    ]
    rows.extend(f"2000,{day},0.0,35,5,1100" for day in range(1, days + 1))
    return (newline.join(rows) + newline).encode()


def test_climate_profile_uses_the_calendar_and_requires_all_source_days():
    location = 43.29925, -89.35245
    spring, summer, dry, vpd, gdd, heat, frost = climate_profile(
        climate_export(newline="\r\n"), location, range(2000, 2001)
    )
    assert (spring, summer, dry, gdd, heat, frost) == (0, 0, 92, 1530, 460, 0)
    assert 1 < vpd < 3
    with pytest.raises(ValueError, match="every pretrial day"):
        climate_profile(climate_export(364), location, range(2000, 2001))
    with pytest.raises(ValueError, match="outside"):
        climate_profile(climate_export(366), location, range(2000, 2001))
    with pytest.raises(ValueError, match="different coordinate"):
        climate_profile(climate_export(), (41, -90), range(2000, 2001))


def test_soil_values_do_not_invent_storage_when_retention_is_missing_or_inverted():
    soil = SoilProfile(
        latitude=43,
        longitude=-90,
        mukey="10",
        field_capacity=0.30,
        wilting_point=0.10,
        drainage="Somewhat poorly drained",
    )
    assert soil_values(soil)[4:6] == (pytest.approx(0.2), 2.0)
    assert soil_values(soil.model_copy(update={"field_capacity": 0.09}))[4] is None
    assert soil_values(None) == (None,) * 10


def test_soil_aggregate_uses_weighted_depth_units_and_rejects_a_different_map_unit():
    def response(row):
        return json.dumps({"Table": [list(SOIL_AGGREGATE_COLUMNS), row]}).encode()

    raw = response(["10", "5.2", "21.3", None, "2.4"])
    assert parse_soil_aggregates(raw, {"10"}) == {"10": (213.0, 161.0, None, 2.4)}
    with pytest.raises(ValueError, match="map unit"):
        parse_soil_aggregates(raw, {"30"})
    with pytest.raises(ValueError, match="decreases with depth"):
        parse_soil_aggregates(response(["10", 23, 21, 10, 0]), {"10"})


def trial_row(name, latitude, target, previous="Soy"):
    return Sample(name, name, latitude, -89.0, 2012, previous, target, 1)


def test_climate_normals_cannot_enter_a_prediction_for_an_earlier_year():
    after_window = trial_row("trial", 41, 0)
    within_window = replace(after_window, year=CLIMATE_YEARS.stop - 1)
    climate = {location_of(after_window): (1.0,) * len(CLIMATE_FEATURES)}

    assert (
        feature_groups(after_window, climate, {})["climate"] == climate[location_of(after_window)]
    )
    assert feature_groups(within_window, climate, {})["climate"] == (None,) * len(CLIMATE_FEATURES)


def test_kbs_matches_observed_predecessor_and_control_in_each_replicate():
    def row(year, plot, rep, crop, value, cover="no cover"):
        return f"{year},{plot},{rep},{cover},conventional,{crop},{year}-11-01,{value}"

    csv = ["# KBS yield export", ",".join(KBS_COLUMNS)]
    for rep in (1, 2, 3):
        csv.extend(
            (
                row(2006, f"{rep}01B", rep, "cont. corn", 7200),
                row(2007, f"{rep}01B", rep, "cont. corn", 8000),
                row(2006, f"{rep}02B", rep, "soybean (C-S)", 2300),
                row(2007, f"{rep}02B", rep, "corn (C-S)", 9000),
                row(2006, f"{rep}03B", rep, "wheat (C-S-W)", 3000),
                row(2007, f"{rep}03B", rep, "corn (C-S-W)", 10000),
            )
        )
    csv[-2] = row(2006, "303B", 3, "wheat (C-S-W)", 3000, cover="cover")
    soy, wheat = kbs_contrasts(("\n".join(csv) + "\n").encode())

    assert (soy.preceding_crop, soy.delta_t_ha, soy.management_pairs) == ("Soy", 1.0, 3)
    assert (wheat.preceding_crop, wheat.delta_t_ha, wheat.management_pairs) == ("Wheat", 2.0, 2)


def test_scaling_and_feature_availability_are_fold_local():
    first, second, third = (
        trial_row("first", 40, 2),
        trial_row("second", 41, 3),
        trial_row("third", 42, 5, "Wheat"),
    )
    features = {
        first: {"geo": (1.0, 8.0)},
        second: {"geo": (3.0, 8.0)},
        third: {"geo": (100.0, -100.0)},
    }
    fitted, held = feature_matrices(
        [first, second], [third], ("geo",), features, np.ones(2), interactions=False
    )
    np.testing.assert_allclose(fitted, [[0, -1], [0, 1]])
    np.testing.assert_allclose(held, [[1, 98]])


def test_missing_group_or_unseen_predecessor_falls_back_to_the_reference():
    first, second, missing, novel, supported = (
        trial_row("first", 40, 2),
        trial_row("second", 41, 3),
        trial_row("missing", 42, 200),
        trial_row("novel", 43, 200, "Wheat"),
        trial_row("supported", 44, 200),
    )
    rows = [first, second, missing, novel, supported]
    features = {row: {"geo": (row.latitude, 0.0)} for row in rows}
    features[missing]["geo"] = (None, None)
    ridge = Candidate("ridge", ("geo",), (("alpha", 1.0),))
    result = predict_fold(rows[:2], rows[2:], features, (REFERENCE, ridge))

    assert result.supported[ridge].tolist() == [False, False, True]
    np.testing.assert_allclose(result.predictions[ridge][:2], [2.5, 2.5])


def test_distance_taper_falls_back_beyond_supported_predecessor_locations():
    fit = [trial_row("a", 40, 2), trial_row("b", 41, 3)]
    evaluation = [trial_row("near", 41.4, 10), trial_row("far", 47, 10)]
    features = {row: {"geo": (row.latitude, 0.0)} for row in fit + evaluation}
    ridge = Candidate("ridge", ("geo",), (("alpha", 1.0),))
    tapered = replace(ridge, taper_km=150)
    fold = predict_fold(fit, evaluation, features, (REFERENCE, ridge, tapered))

    assert fold.supported[tapered].tolist() == [True, False]
    assert 2.5 < fold.predictions[tapered][0] < fold.predictions[ridge][0]
    assert fold.predictions[tapered][1] == 2.5


def test_nested_tuning_does_not_see_the_outer_farms_targets():
    rows = [
        trial_row(name, 40 + index, target)
        for index, (name, target) in enumerate((("a", 2), ("b", 4), ("c", 1), ("d", 6)))
    ]
    features = {row: {"geo": (row.latitude, 0.0)} for row in rows}
    ridge = Candidate("ridge", ("geo",), (("alpha", 1.0),))
    original = validate(rows, features, (REFERENCE, ridge))
    changed = [replace(rows[0], delta_t_ha=25), *rows[1:]]
    features[changed[0]] = features[rows[0]]
    perturbed = validate(changed, features, (REFERENCE, ridge))

    assert original[0].evaluation.rows[0].farm == "a"
    assert original[0].fit_scores == perturbed[0].fit_scores
    assert original[1].fit_scores != perturbed[1].fit_scores


def test_search_combinations_are_unique_and_cover_all_feature_subsets():
    candidates = search_space()

    assert len({candidate.key for candidate in candidates}) == len(candidates)
    assert len({candidate.groups for candidate in candidates}) == 16
    assert {candidate.family for candidate in candidates} == {
        "mean",
        "ridge_v1",
        "ridge",
        "elastic_net",
        "huber",
        "kernel_ridge",
        "extra_trees",
        "gradient_boosting",
    }
