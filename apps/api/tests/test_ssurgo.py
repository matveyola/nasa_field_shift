from datetime import date

import pytest

from field_shift.modules.geo_ml import ssurgo


def test_point_profile_uses_representative_horizon_and_converts_volumetric_units() -> None:
    payload = {
        "Table": [
            list(ssurgo._COLUMNS),
            [
                "2877332",
                "Clarion loam",
                "28645790",
                "Clarion",
                "85",
                "Well drained",
                "0",
                "15",
                "51",
                "31",
                "18",
                "2.5",
                "1.3",
                "26.7",
                "11.9",
                "2026-09-08",
            ],
        ]
    }
    profile = ssurgo.parse_soil_profile(payload, 41.705785, -93.410507)
    assert profile is not None
    assert (profile.sand, profile.silt, profile.clay) == (51, 31, 18)
    assert (profile.field_capacity, profile.wilting_point) == pytest.approx((0.267, 0.119))
    assert (profile.horizon_top_cm, profile.horizon_bottom_cm) == (0, 15)
    assert profile.survey_date == date(2026, 9, 8)
    assert profile.mukey == "2877332"


def test_missing_and_malformed_provider_responses_are_distinct() -> None:
    assert ssurgo.parse_soil_profile({"Table": [list(ssurgo._COLUMNS)]}, 41.7, -93.4) is None
    with pytest.raises(ssurgo.SoilSurveyError):
        ssurgo.parse_soil_profile({"Table": [["unexpected"], ["1"]]}, 41.7, -93.4)
    with pytest.raises(ValueError, match="WGS84"):
        ssurgo.fetch_soil_profile(float("nan"), -93.4)


def test_provider_cache_is_coordinate_keyed_bounded_and_periodically_revalidated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    clock = [0]

    def fetch(latitude: float, longitude: float) -> ssurgo.SoilProfile:
        calls.append((latitude, longitude))
        return ssurgo.SoilProfile(latitude=latitude, longitude=longitude, mukey="2877332")

    ssurgo._cached_profile.cache_clear()
    monkeypatch.setattr(ssurgo, "_fetch_profile", fetch)
    monkeypatch.setattr(ssurgo, "monotonic", lambda: clock[0])
    try:
        assert ssurgo.fetch_soil_profile(41.7057851, -93.4105071) == (
            ssurgo.fetch_soil_profile(41.7057852, -93.4105072)
        )
        assert calls == [(41.705785, -93.410507)]
        clock[0] += 24 * 3600
        ssurgo.fetch_soil_profile(41.705785, -93.410507)
        ssurgo.fetch_soil_profile(41.705786, -93.410507)
        assert calls == [(41.705785, -93.410507), (41.705785, -93.410507), (41.705786, -93.410507)]
        assert ssurgo._cached_profile.cache_info().maxsize == 512
    finally:
        ssurgo._cached_profile.cache_clear()
