from itertools import product
from uuid import uuid4

import pytest
from pydantic import ValidationError

from field_shift.modules.rotations.planner import optimize
from field_shift.modules.rotations.planning_schemas import (
    CropDecision,
    PlanningField,
    PlanningRequest,
)


def field(field_id="F-1", area=10, crop="Wheat", season=2026):
    return PlanningField(
        id=field_id,
        name=field_id,
        area_ha=area,
        boundary_revision=0,
        crop=crop,
        crop_season=season,
        history=[CropDecision(field_id=field_id, season=season, crop=crop)],
    )


def request(**overrides):
    return PlanningRequest.model_validate(
        {
            "client_id": str(uuid4()),
            "start_season": 2027,
            "field_ids": ["F-1"],
            "crops": [{"crop": "Wheat"}, {"crop": "Soy"}, {"crop": "Maize"}],
            **overrides,
        }
    )


def test_optimizer_matches_independent_exhaustive_optimum_with_quotas_and_locks():
    fields = [field("F-1", 10, "Wheat"), field("F-2", 20, "Soy")]
    payload = request(
        field_ids=["F-1", "F-2"],
        crops=[
            {"crop": "Wheat", "preference": 3, "max_area_ha": 20},
            {"crop": "Soy", "preference": 5, "min_area_ha": 10, "max_share_percent": 70},
            {"crop": "Maize", "preference": 1},
        ],
        locked=[{"field_id": "F-2", "season": 2027, "crop": "Wheat"}],
    )
    # Independent oracle enumerates all cells, without using production constraint helpers.
    feasible_scores = []
    names = ["Wheat", "Soy", "Maize"]
    for cells in product(names, repeat=6):
        first, second = cells[:3], cells[3:]
        if first[0] == "Wheat" or second[0] != "Wheat":
            continue
        if any(a == b for row in (first, second) for a, b in zip(row, row[1:])):
            continue
        amounts = [
            {name: 10 * (first[index] == name) + 20 * (second[index] == name) for name in names}
            for index in range(3)
        ]
        if any(row["Wheat"] > 20 or not 10 <= row["Soy"] <= 21 for row in amounts):
            continue
        preference = {"Wheat": 3, "Soy": 5, "Maize": 1}
        feasible_scores.append(
            sum(amount * preference[name] for row in amounts for name, amount in row.items())
        )
    result = optimize(payload, fields)
    assert result.status == "optimal"
    assert result.objective == max(feasible_scores)
    assert result.assignments[3].locked
    assert result.assignments[3].crop == "Wheat"
    assert all(10 <= row.area_ha <= 21 for row in result.areas if row.crop == "Soy")
    repeated = optimize(payload, list(reversed(fields)))
    assert result == repeated


def test_whole_field_area_constraints_do_not_silently_split_or_relax():
    payload = request(
        crops=[
            {"crop": "Soy", "min_area_ha": 5, "max_area_ha": 5},
            {"crop": "Maize", "min_area_ha": 5, "max_area_ha": 5},
        ]
    )
    result = optimize(payload, [field()])
    assert result.status == "infeasible"
    assert result.assignments == []
    assert result.diagnostics[0].code == "no_field_sequence"


def test_historical_return_interval_family_rules_and_impossible_locks_are_hard():
    payload = request(
        crops=[{"crop": "Soy", "min_break_years": 2}, {"crop": "Wheat"}, {"crop": "Maize"}],
        history=[{"field_id": "F-1", "season": 2025, "crop": "Soy"}],
        avoid_consecutive_family=True,
    )
    # Soy cannot follow 2025 soy in 2027 with two intervening years; both other
    # crops share the 2026 wheat family. It must fail, not silently loosen a rule.
    result = optimize(payload, [field()])
    assert result.status == "infeasible"
    locked = request(locked=[{"field_id": "F-1", "season": 2027, "crop": "Wheat"}])
    assert optimize(locked, [field()]).status == "infeasible"


def test_missing_history_is_explicit_and_farmer_history_is_used():
    payload = request(crops=[{"crop": "Wheat", "min_break_years": 3}, {"crop": "Soy"}])
    result = optimize(payload, [field(crop="Maize")])
    assert result.warnings[0].code == "incomplete_history"
    assert result.status == "optimal"
    override = request(
        crops=[{"crop": "Wheat", "min_break_years": 3}, {"crop": "Soy"}],
        history=[{"field_id": "F-1", "season": 2026, "crop": "Wheat"}],
    )
    assert optimize(override, [field(crop="Maize")]).status == "infeasible"


def test_resource_limit_is_not_reported_as_proof_of_infeasibility_or_optimality():
    limited = optimize(request(), [field()], node_limit=1)
    assert limited.status == "search_limit"
    assert limited.objective is None
    assert limited.assignments == []
    feasible = optimize(request(), [field()], node_limit=3)
    assert feasible.status == "feasible"
    assert len(feasible.assignments) == 3
    assert feasible.explored_nodes <= 3


@pytest.mark.parametrize(
    "override",
    [
        {"field_ids": ["F-1", "F-1"]},
        {"field_ids": []},
        {"crops": [{"crop": "Soy"}, {"crop": "Soy"}]},
        {"crops": [{"crop": "Soy", "max_share_percent": float("nan")}, {"crop": "Wheat"}]},
        {"crops": [{"crop": "Soy", "min_area_ha": 12, "max_area_ha": 1}, {"crop": "Wheat"}]},
        {"locked": [{"field_id": "not-selected", "season": 2027, "crop": "Soy"}]},
        {"locked": [{"field_id": "F-1", "season": 2030, "crop": "Soy"}]},
        {"locked": [{"field_id": "F-1", "season": 2027, "crop": "Peas"}]},
        {"history": [{"field_id": "F-1", "season": 2027, "crop": "Soy"}]},
        {"name": "  "},
        {"unknown_option": True},
    ],
)
def test_malformed_planning_constraints_are_rejected(override):
    with pytest.raises(ValidationError):
        request(**override)


def test_impossible_farm_quotas_give_specific_diagnostics():
    minimum = request(
        crops=[{"crop": "Wheat", "min_area_ha": 8}, {"crop": "Soy", "min_area_ha": 8}]
    )
    assert optimize(minimum, [field()]).diagnostics[0].code == "minimum_area_exceeds_total"
    maximum = request(
        crops=[{"crop": "Wheat", "max_share_percent": 40}, {"crop": "Soy", "max_share_percent": 40}]
    )
    assert optimize(maximum, [field()]).diagnostics[0].code == "maximum_area_below_total"
