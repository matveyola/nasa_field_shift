from datetime import date, datetime

import pytest

from field_shift.modules.simulation.root_zone_inputs import RootZoneRun, RootZoneWeatherDay
from field_shift.modules.simulation.root_zone_model import CAPACITY_MM
from field_shift_ml.fetch_root_zone_observations import RootZoneSite
from field_shift_ml.research_root_zone import (
    ALTERNATIVES,
    choose,
    fit_rows,
    predictions,
    spatial_taper,
    uscrn_profiles,
)
from field_shift_ml.train_root_zone import Sample


def sample(station: str, latitude: float, longitude: float) -> Sample:
    day = RootZoneWeatherDay(date(2026, 6, 2), 1, 0.0, 2.0, 20.0, 18.0, (0.35,) * 3)
    run = RootZoneRun(date(2026, 6, 1), latitude, longitude, "50R1", (0.35,) * 3, (day,))
    return Sample(
        RootZoneSite(station, station, latitude, longitude, -6, "fit"),
        run,
        day,
        CAPACITY_MM,
        110.0,
        CAPACITY_MM,
    )


def test_geographic_fold_excludes_same_block_and_near_sites_across_boundary():
    held = sample("held", 41.9, -95)
    same_block = sample("same", 40.1, -95)
    across_boundary = sample("near", 42.01, -95)
    distant = sample("fit", 43.5, -95)

    assert fit_rows([held, same_block, across_boundary, distant], held.site, 40) == [distant]


def test_taper_returns_to_direct_ifs_outside_support_and_preserves_total_storage():
    train = sample("train", 41.9, -95)
    far = sample("far", 46, -95)
    nearby = sample("near", 42.5, -95)

    taper = spatial_taper([train], [nearby, far], 300)
    assert taper.tolist() == pytest.approx([0.778, 0], abs=0.001)
    targets = {"train": {train.day.date: 450.0}}
    output = predictions([train], [nearby, far], targets, ("raw_ifs", "ifs_ridge_local300"))
    assert output["raw_ifs"][1].tolist() == pytest.approx([350.0, 350.0])
    reserve, storage = output["ifs_ridge_local300"]
    assert reserve.tolist() == [CAPACITY_MM, CAPACITY_MM]
    assert storage[0] > 350
    assert storage[1] == pytest.approx(350.0)


def test_candidate_selection_requires_both_targets_and_site_stability():
    def metrics(reserve, storage):
        return {
            "reserve_mm": {"mae": reserve, "rmse": reserve},
            "total_storage_mm": {"mae": storage, "rmse": storage} if storage is not None else None,
        }

    overall = dict.fromkeys(("raw_ifs", *ALTERNATIVES), metrics(100, 120))
    overall.update(
        ifs_bias=metrics(30, 110),
        ifs_ridge=metrics(40, 125),
        ifs_ridge_local300=metrics(70, 110),
        fao56_ridge_v1=metrics(20, None),
    )
    groups = {"site": overall | {"ifs_bias": metrics(111, 115)}}

    assert choose(overall, groups) == ("ifs_ridge_local300", ["ifs_ridge_local300"])


def test_noaa_profile_requires_all_five_depths_and_uses_start_of_utc_hour():
    columns = ["13301", "20260819", "0000", *(["0"] * 35)]
    columns[28:33] = ["0.1", "0.2", "0.3", "0.4", "0.5"]
    complete = " ".join(columns)
    columns[32] = "-99"
    missing = " ".join(columns)

    ((stamp, storage),) = list(uscrn_profiles(f"{complete}\n{missing}", "USCRN:13301"))

    assert stamp == datetime(2026, 8, 18, 23)
    assert storage == pytest.approx(0.3675)
