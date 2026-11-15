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


@pytest.mark.parametrize("season_count", [1, 2, 4, 6])
def test_selected_horizon_keeps_last_season_locks_and_independent_optimum(season_count):
    names = ["Soy", "Maize"]
    payload = request(
        season_count=season_count,
        require_two_crops=season_count > 1,
        crops=[{"crop": "Soy", "preference": 3}, {"crop": "Maize", "preference": 1}],
        locked=[{"field_id": "F-1", "season": 2026 + season_count, "crop": "Soy"}],
    )
    scores = [
        10 * sum(3 if crop == "Soy" else 1 for crop in row)
        for row in product(names, repeat=season_count)
        if row[-1] == "Soy" and all(a != b for a, b in zip(row, row[1:]))
    ]
    result = optimize(payload, [field()])
    assert result.status == "optimal"
    assert result.objective == max(scores)
    assert len(result.assignments) == season_count
    assert [row.season for row in result.assignments] == list(range(2027, 2027 + season_count))
    assert result.assignments[-1].locked and result.assignments[-1].crop == "Soy"
    assert len(result.areas) == 2 * season_count


def test_one_crop_one_season_and_explicit_diversity_conflict():
    payload = request(season_count=1, require_two_crops=False, crops=[{"crop": "Soy"}])
    assert optimize(payload, [field()]).status == "optimal"
    with pytest.raises(ValidationError, match="disable the two-crops-per-field"):
        request(season_count=1, crops=[{"crop": "Soy"}])
    # A requested diversity rule for a longer one-species plan is not relaxed.
    repeated = request(crops=[{"crop": "Soy", "min_break_years": 0}])
    assert optimize(repeated, [field()]).status == "infeasible"


@pytest.mark.parametrize(
    "override",
    [
        {"season_count": 0},
        {"season_count": 7},
        {"season_count": True},
        {"season_count": "2"},
        {"start_season": 2096, "season_count": 6},
        {"season_count": 2, "locked": [{"field_id": "F-1", "season": 2029, "crop": "Soy"}]},
    ],
)
def test_variable_horizon_rejects_invalid_scope(override):
    with pytest.raises(ValidationError):
        request(**override)
    assert request().season_count == 3
    assert request(start_season=2095, season_count=6).season_count == 6


def test_large_catalog_horizon_and_field_set_is_bounded_and_returns_complete_incumbent():
    from field_shift.modules.rotations.planning_catalog import CROP_FAMILIES

    fields = [field(f"F-{i:02d}") for i in range(30)]
    payload = request(
        season_count=6,
        field_ids=[f.id for f in fields],
        crops=[{"crop": crop} for crop in CROP_FAMILIES],
    )
    result = optimize(payload, fields)
    assert result.status == "feasible"  # Truncated generation never proves optimality.
    assert not result.generation_complete
    assert result.generation_nodes <= result.generation_limit == 60_000
    assert result.explored_nodes <= result.node_limit == 120_000
    assert len(result.assignments) == 180
    assert len({(row.field_id, row.season) for row in result.assignments}) == 180
    assert {r.field_id for r in result.warnings if r.code == "generation_budget_exhausted"} == {
        f.id for f in fields
    }
    for f in fields:
        rows = [r for r in result.assignments if r.field_id == f.id]
        assert rows[0].crop != "Wheat"
        assert all(a.crop != b.crop for a, b in zip(rows, rows[1:]))
    assert result == optimize(payload, list(reversed(fields)))


def test_generation_counts_rejected_prefixes_and_cannot_claim_infeasibility_when_cut_off():
    # Alphabetically Maize is examined before Soy: the first rejected edge uses
    # the entire budget; unexamined Soy would be a feasible single-season plan.
    payload = request(
        season_count=1,
        require_two_crops=False,
        crops=[{"crop": "Maize"}, {"crop": "Soy"}],
    )
    result = optimize(payload, [field(crop="Maize")], generation_limit=1)
    assert result.generation_nodes == 1
    assert result.status == "search_limit"
    assert not result.generation_complete and not result.assignments
    full = optimize(payload, [field(crop="Maize")])
    assert full.status == "optimal" and full.assignments[0].crop == "Soy"


def test_small_generation_quota_is_shared_across_fields():
    fields = [field("F-1"), field("F-2")]
    payload = request(
        field_ids=[f.id for f in fields],
        season_count=1,
        require_two_crops=False,
        crops=[{"crop": "Maize"}, {"crop": "Soy"}],
    )
    result = optimize(payload, fields, generation_limit=2)
    assert result.status == "feasible"
    assert result.generation_nodes == 2
    assert {row.field_id for row in result.assignments} == {"F-1", "F-2"}
    assert not result.generation_complete


def test_generation_and_search_share_one_deadline():
    class Clock:
        calls = 0

        def __call__(self):
            self.calls += 1
            return 0.0 if self.calls <= 2 else 5.0

    result = optimize(request(), [field()], clock=Clock())
    assert result.generation_nodes == 1
    assert result.explored_nodes == 0
    assert result.status == "search_limit"
    assert not result.generation_complete


def test_truncated_candidates_cannot_prove_farm_quota_infeasible():
    fields = [field("F-1"), field("F-2")]
    payload = request(
        field_ids=[f.id for f in fields],
        season_count=1,
        require_two_crops=False,
        crops=[{"crop": "Maize"}, {"crop": "Soy", "min_area_ha": 10}],
    )
    # Each quota admits only Maize; Soy outside the enumerated subset would meet
    # the farm minimum. A selector prune on that subset is not global proof.
    partial = optimize(payload, fields, generation_limit=2)
    assert partial.status == "search_limit"
    assert not partial.generation_complete and not partial.assignments
    complete = optimize(payload, fields)
    assert complete.status == "optimal"
    assert sum(row.area_ha for row in complete.assignments if row.crop == "Soy") >= 10


@pytest.mark.parametrize("season_count", [1, 2, 6])
def test_setup_crop_restrictions_survive_variable_horizons_and_bounded_generation(season_count):
    scoped = field().model_copy(update={"allowed_crops": ["Soy", "Maize"]})
    payload = request(
        season_count=season_count,
        require_two_crops=season_count > 1,
        crops=[{"crop": "Soy"}, {"crop": "Maize"}, {"crop": "Sunflower", "preference": 10}],
    )
    result = optimize(payload, [scoped])
    assert result.status == "optimal"
    assert len(result.assignments) == season_count
    assert {row.crop for row in result.assignments} <= {"Soy", "Maize"}
    assert all("Allowed by the field setup" in row.reasons for row in result.assignments)
    disallowed = payload.model_copy(
        update={
            "locked": [
                CropDecision(field_id=scoped.id, season=payload.start_season, crop="Sunflower")
            ]
        }
    )
    rejected = optimize(disallowed, [scoped])
    assert rejected.status == "infeasible" and not rejected.assignments
