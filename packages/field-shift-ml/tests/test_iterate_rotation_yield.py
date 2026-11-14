from dataclasses import replace

import numpy as np
import pytest

from field_shift_ml.iterate_rotation_yield import (
    BUSHELS_PER_ACRE_TO_T_HA,
    extrapolation_contrasts,
    farm_folds,
    morrow_contrasts,
    predictions,
    source_rows,
)
from field_shift_ml.train_rotation_yield import Sample


def test_farm_folds_keep_co_located_trials_together():
    north = Sample("north", "a", 40, -88, 2012, "Soy", 2, 1)
    south = replace(north, site="south", preceding_crop="Wheat")
    independent = replace(north, site="east", farm="b")

    assert list(farm_folds([independent, north, south])) == [
        ([independent], [north, south]),
        ([north, south], [independent]),
    ]


def test_missing_training_predecessor_uses_farm_balanced_mean_for_every_candidate():
    soy = Sample("north", "a", 40, -88, 2012, "Soy", 2, 1)
    fit = [soy, replace(soy, year=2013, delta_t_ha=4), replace(soy, farm="b", delta_t_ha=9)]
    evaluation = [replace(soy, farm="c", preceding_crop="Wheat", delta_t_ha=-20)]

    for values in predictions(fit, evaluation).values():
        np.testing.assert_allclose(values, [6])


def morrow_row(plot, year, crop, yield_bush, nit="200"):
    return {
        "plot": plot,
        "plot_num": plot[0],
        "phase": "5",
        "year": str(year),
        "crop": crop,
        "treatment": "LNPK",
        "nit": nit,
        "yield_bush": str(yield_bush),
    }


def test_morrow_keeps_same_nitrogen_and_observed_predecessor_with_matching_control():
    rows = [
        morrow_row("4NA", 2019, "S", 70),
        morrow_row("4NA", 2020, "C", 170),
        morrow_row("3NA", 2020, "CC", 150),
        morrow_row("4NB", 2019, "S", 70),
        morrow_row("4NB", 2020, "C", 180),
        morrow_row("3NB", 2020, "CC", 120, nit="100"),
        morrow_row("4NC", 2020, "C", 180),
        morrow_row("3NC", 2020, "CC", 140),
        morrow_row("4SA", 2019, "S", 70),
        morrow_row("4SA", 2020, "C", 190),
        morrow_row("3SA", 2020, "CC", 160),
    ]
    [contrast] = morrow_contrasts(rows)

    assert contrast.year == 2020
    assert contrast.preceding_crop == "Soy"
    assert contrast.management_pairs == 2
    assert contrast.delta_t_ha == pytest.approx(25 * BUSHELS_PER_ACRE_TO_T_HA)


def test_extrapolation_collapses_strata_and_excludes_non_independent_and_low_n_ne_rows():
    row = {
        "site": "NE",
        "year": "2020",
        "nitrogen": "SN",
        "ECRB_Exp": "1.2",
        "latitude": "41.167",
        "longitude": "-96.419",
    }
    rows = [
        row,
        {**row, "ECRB_Exp": "1.6"},
        {**row, "site": "OH1", "ECRB_Exp": "4.6"},
        {**row, "nitrogen": "LN", "ECRB_Exp": "5.6"},
    ]

    [contrast] = extrapolation_contrasts(rows)
    assert contrast.farm == "NE"
    assert contrast.management_pairs == 2
    assert contrast.delta_t_ha == pytest.approx(1.4)


def test_cached_research_source_rejects_digest_mismatch(tmp_path):
    path = tmp_path / "research.csv"
    path.write_bytes(b"unverified")

    with pytest.raises(ValueError, match="pinned research export"):
        source_rows(path, "https://example.com/research.csv", "wrong digest")
