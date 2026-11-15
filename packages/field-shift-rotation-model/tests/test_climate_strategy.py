from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from field_shift_rotation_model import network, runtime
from field_shift_rotation_model.modules.rotations.climate_planner import ClimateStepRequest

SUTHERLAND = {"field_id": "sutherland_nw", "planting_date": "2040-05-01"}


def test_sequence_acquires_and_replays_crops_and_catalogue_hybrids(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Packaged Sutherland weather should require no provider request")

    monkeypatch.setattr(network, "real_build_opener", denied)
    snapshot = runtime.acquire(
        {
            "operation": "climate_step",
            "parameters": SUTHERLAND | {"strategy_mode": "sequence"},
            "decision_date": datetime.now(UTC).date().isoformat(),
            "land_status": "arable",
        }
    )
    report = runtime.evaluate(snapshot)
    result = report["payload"]
    assert report["ranking_usable"] is True
    assert result["max_steps"] == 6
    assert len(result["candidates"]) == 17
    assert result["eligible_count"] == len(result["top_candidates"])
    assert {c["crop_id"] for c in result["top_candidates"]} >= {"peas_dry", "oats", "millet"}
    assert len({c["crop_id"] for c in result["top_candidates"]}) == 10
    assert len({c["choice_id"] for c in result["top_candidates"]}) == result["eligible_count"]
    assert all(c["scenarios"] for c in result["top_candidates"])
    assert {c["choice_id"] for c in result["candidates"] if c["choice"].get("hybrid")} == {
        h["choice_id"] for h in runtime.catalog()["strategy_hybrids"]
    }
    deficits = [c["water"]["water_deficit_pct"] for c in result["top_candidates"]]
    assert deficits == sorted(deficits)
    assert result["irrigation"]["status"] == "unknown"
    assert all(c["water"]["scheduled_irrigation_mm"] is None for c in result["top_candidates"])


def test_compare_uses_shared_opening_conditions_and_actual_irrigation():
    parameters = SUTHERLAND | {
        "strategy_mode": "compare",
        "candidates": [{"crop_id": "maize_grain"}, {"crop_id": "sunflower"}],
        "observed_crop_history": [
            {"season_year": 2039, "crop_id": "soybeans", "planting_date": "2039-05-12"}
        ],
        "farmer": {"priorities": ["water_saving"]},
    }
    rainfed, irrigated, unknown_volume = (
        runtime.calculate(
            "climate_step",
            parameters | {"irrigation": irrigation},
            date(2026, 11, 15),
        )
        for irrigation in (
            {"status": "none"},
            {"status": "available", "daily_mm": 3.0},
            {"status": "available", "method": "drip"},
        )
    )
    assert rainfed["ranking"]["status"] == "priority_unavailable"
    assert rainfed["top_candidates"] == []
    assert rainfed["ranking"]["selection_ids"] == ["maize_grain", "sunflower"]
    assert unknown_volume["ranking"]["status"] == "priority_unavailable"
    assert unknown_volume["irrigation"]["status"] == "available"
    assert all(c["water"]["scheduled_irrigation_mm"] is None for c in unknown_volume["candidates"])
    assert rainfed["observed_previous_crop"]["planting_date"] == "2039-05-12"
    assert rainfed["observed_previous_crop"]["fallow_days_before_first_planting"] is None
    assert not irrigated["has_next_step_after_selection"]
    assert irrigated["ranking"]["status"] == "ranked"
    assert len(irrigated["candidates"]) == 2
    one, two = irrigated["candidates"]
    assert one["scenarios"].keys() == two["scenarios"].keys()
    for case, first in one["scenarios"].items():
        assert first["profile_opening_mm"] == two["scenarios"][case]["profile_opening_mm"]
    for dry, wet in zip(rainfed["candidates"], irrigated["candidates"], strict=True):
        assert dry["water"]["scheduled_irrigation_mm"] == 0
        assert wet["water"]["scheduled_irrigation_mm"] > 0
        assert wet["water"]["water_deficit_mm"] < dry["water"]["water_deficit_mm"]
        for member in wet["scenarios"].values():
            assert (
                0
                < member["metrics"]["scheduled_irrigation_mm"]
                <= 3 * member["metrics"]["crop_days"]
            )


def test_strategy_validates_distinct_crops_dates_and_supported_progression():
    selected = [{"crop_id": "oats", "planting_date": f"{year}-05-01"} for year in range(2022, 2027)]
    request = {"field_id": "sutherland_nw", "planting_date": "2027-05-01", "selected": selected}
    assert (
        len(ClimateStepRequest.model_validate(request | {"strategy_mode": "sequence"}).selected)
        == 5
    )
    with pytest.raises(ValidationError, match="2040"):
        ClimateStepRequest.model_validate(request)
    with pytest.raises(ValidationError, match="2022"):
        ClimateStepRequest.model_validate(
            SUTHERLAND | {"strategy_mode": "sequence", "planting_date": "2021-05-01"}
        )
    with pytest.raises(ValidationError, match="supported hybrid"):
        ClimateStepRequest.model_validate(
            SUTHERLAND
            | {
                "strategy_mode": "compare",
                "candidates": [
                    {"crop_id": "oats", "choice_id": "1"},
                    {"crop_id": "oats", "choice_id": "2"},
                ],
            }
        )
    with pytest.raises(ValidationError, match="greater than 0"):
        ClimateStepRequest.model_validate(
            SUTHERLAND
            | {
                "strategy_mode": "sequence",
                "irrigation": {"status": "available", "daily_mm": 0},
            }
        )


def test_compare_calculates_each_documented_maize_hybrid_separately():
    hybrids = [{"crop_id": "maize-p0987amx"}, {"crop_id": "maize-p1197amxt"}]
    parameters = SUTHERLAND | {"strategy_mode": "compare", "candidates": hybrids}
    with pytest.raises(ValidationError, match="distinct choice_id"):
        ClimateStepRequest.model_validate(parameters | {"candidates": hybrids[:1] * 2})
    first, second = runtime.calculate("climate_step", parameters, date(2026, 11, 15))["candidates"]
    assert first["crop_id"] == second["crop_id"] == "maize_grain"
    assert [first["choice_id"], second["choice_id"]] == [h["crop_id"] for h in hybrids]
    assert first["water"]["water_deficit_pct"] != second["water"]["water_deficit_pct"]
