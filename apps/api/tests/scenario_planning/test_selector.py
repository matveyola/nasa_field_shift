"""Independent tiny exhaustive oracle plus failures on synthetic DTO fixtures."""

import copy
import itertools

import pytest

from field_shift.modules.rotations.scenario_planning.economics import aggregate
from field_shift.modules.rotations.scenario_planning.explanation import evidence_pack, explain
from field_shift.modules.rotations.scenario_planning.selector import BoundedSelector, Cancelled

from .conftest import validated


def test_independent_oracle_rejects_greedy_farm(farm_data):
    r, _, _ = farm_data
    r["crop_areas"] = [
        dict(
            constraint_id="wheat-area",
            season=2027,
            crop_id="Wheat",
            min_ha=0.0,
            max_ha=10.0,
            max_share=None,
        )
    ]
    r["resources"] = [
        dict(constraint_id="budget", season=2027, budget=2300.0, currency="USD", irrigation_m3=0.0)
    ]
    request, candidates, evaluations = validated(farm_data)
    result = BoundedSelector().compare(request, candidates, evaluations)
    # Oracle does not use selector, constraint or economics implementation.
    expected = []
    for choices in itertools.product(["Wheat", "Soy"], repeat=3):
        if choices.count("Wheat") * 10 > 10:
            continue
        cost = sum(1000 if c == "Wheat" else 600 for c in choices)
        if cost <= 2300:
            expected.append(sum(4000 if c == "Wheat" else 3400 for c in choices))
    assert result.solver_status == "optimal"
    assert result.alternatives[0].objective_value == max(expected) == 10800
    assert len(result.alternatives) == 3
    assert all(
        sum(a.crop.crop_id == "Wheat" for a in p.assignments) == 1 for p in result.alternatives
    )


@pytest.mark.parametrize(
    "goal,expected", [("reference_margin", 12000), ("worst_case_margin", 7200), ("water_demand", 0)]
)
def test_three_policies(farm_data, goal, expected):
    farm_data[0]["goal"] = goal
    result = BoundedSelector().compare(*validated(farm_data))
    assert result.alternatives[0].objective_value == expected
    assert result.solver_status == "optimal"
    assert len({p.alternative_id for p in result.alternatives}) == 3


def test_missing_economics_is_null_not_zero(farm_data):
    farm_data[0]["economics"] = []
    request, candidates, evaluations = validated(farm_data)
    rows = aggregate(
        request,
        [candidates[0], candidates[2], candidates[4]],
        {e.sequence_id: e for e in evaluations},
    )
    assert all(
        r.gross_margin.status == "insufficient_data"
        and r.gross_margin.value is None
        and r.currency is None
        for r in rows
    )
    result = BoundedSelector().compare(request, candidates, evaluations)
    assert result.solver_status == "not_run"
    assert not result.alternatives


def test_unknown_price_under_water_policy_preserves_unavailable_money(farm_data):
    farm_data[0]["goal"] = "water_demand"
    farm_data[0]["economics"] = []
    result = BoundedSelector().compare(*validated(farm_data))
    assert result.alternatives
    assert all(r.gross_margin.value is None for r in result.alternatives[0].scenario_results)


def test_lock_and_budget_contradiction(farm_data):
    r, c, _ = farm_data
    lock = c[0]["assignments"][0]
    lock.update(locked=True, commitment_id="seed-contract-1")
    r["locks"] = [copy.deepcopy(lock)]
    r["resources"] = [
        dict(
            constraint_id="zero-budget", season=2027, budget=0.0, currency="USD", irrigation_m3=0.0
        )
    ]
    result = BoundedSelector().compare(*validated(farm_data))
    assert result.solver_status == "infeasible"
    assert {d.code for d in result.diagnostics} >= {"lock_conflict", "budget"}
    assert any(d.evidence[0].id == "zero-budget" for d in result.diagnostics)


@pytest.mark.parametrize("change", ["missing", "unsupported", "unchecked", "future", "member"])
def test_unavailable_provider_data_never_proves_optimality(farm_data, change):
    r, c, e = farm_data
    if change == "missing":
        e.clear()
    elif change == "unsupported":
        for row in c:
            row["assessments"][0]["status"] = "unsupported"
    elif change == "unchecked":
        for row in c:
            row["assessments"][0]["unchecked_rule_ids"] = ["return-period"]
    elif change == "future":
        for row in e:
            row["as_of"] = "2026-11-15T00:00:00Z"
    else:
        for row in e:
            row["seasons"].pop()
    result = BoundedSelector().compare(*validated(farm_data))
    assert result.solver_status == "not_run"
    assert not result.alternatives


def test_node_limit_and_cancellation(farm_data):
    farm_data[0]["solver_limits"]["max_nodes"] = 5
    result = BoundedSelector().compare(*validated(farm_data))
    assert result.solver_status == "limit_reached"
    with pytest.raises(Cancelled):
        BoundedSelector(cancelled=lambda: True).compare(*validated(farm_data))


def test_time_limit(farm_data):
    ticks = itertools.count(0, 40)
    result = BoundedSelector(clock=lambda: next(ticks)).compare(*validated(farm_data))
    assert result.solver_status == "limit_reached"
    assert not result.alternatives


def test_shared_trajectory_not_independent_field_draws(farm_data):
    r, c, e = validated(farm_data)
    rows = aggregate(r, [c[0], c[2], c[4]], {v.sequence_id: v for v in e})
    assert [(v.trajectory_id, v.gross_margin.value) for v in rows] == [
        ("reference", 12000),
        ("dry", 0),
    ]


def test_irrigation_limits_and_cost_not_free(farm_data):
    r, _, evaluations = farm_data
    for f in r["fields"]:
        f["management"]["irrigation"][0].update(system="sprinkler", max_mm=150.0)
    for e in evaluations:
        for row in e["seasons"]:
            row["irrigation_mm"]["value"] = 100.0
    for assumption in r["economics"]:
        assumption["irrigation_cost_per_mm_ha"] = 2.0
    result = BoundedSelector().compare(*validated(farm_data))
    assert result.alternatives[0].objective_value == 6000
    assert result.alternatives[0].scenario_results[0].irrigation_m3 == 30000
    r["resources"] = [
        dict(
            constraint_id="farm-water",
            season=2027,
            budget=None,
            currency=None,
            irrigation_m3=29999.0,
        )
    ]
    result = BoundedSelector().compare(*validated(farm_data))
    assert result.solver_status == "infeasible"
    assert any(d.code == "farm_irrigation" for d in result.diagnostics)
    for f in r["fields"]:
        f["management"]["irrigation"][0].update(system="none", max_mm=0.0)
    assert not BoundedSelector().compare(*validated(farm_data)).alternatives


def test_equipment_unknown_and_capacity(farm_data):
    r, c, _ = farm_data
    window = dict(start="2027-04-01", end="2027-04-30")
    r["equipment"] = [
        dict(constraint_id="tractor-hours", equipment_id="tractor", window=window, capacity_ha=20.0)
    ]
    for f in r["fields"]:
        f["management"]["equipment_ids"] = ["tractor"]
    for seq in c:
        seq["assignments"][0]["sowing_window"] = window
        seq["assignments"][0]["harvest_window"] = dict(start="2027-09-01", end="2027-09-30")
    result = BoundedSelector().compare(*validated(farm_data))
    assert result.solver_status == "infeasible"
    assert any(d.evidence[0].id == "tractor-hours" for d in result.diagnostics)


@pytest.mark.parametrize("mode", ["unavailable", "guarantee", "number", "valid"])
def test_explanation_fidelity(farm_data, mode):
    comparison = BoundedSelector().compare(*validated(farm_data))
    plan = comparison.alternatives[0]
    baseline = explain(comparison, plan.alternative_id)
    pack = evidence_pack(comparison, plan.alternative_id)

    class Provider:
        def explain(self, value, alternative_id):
            import json

            if mode == "unavailable":
                raise ConnectionError()
            if mode == "guarantee":
                return "Guaranteed profit 100000!"
            if mode == "number":
                return '{"references": ["invented-42"]}'
            return json.dumps({"references": list(pack["facts"])})

    assert explain(comparison, plan.alternative_id, Provider()) == baseline
    assert "guaranteed return" in baseline and "12000" in baseline


def test_g23_evidence_preserves_yield_interval_and_nulls(farm_data):
    from field_shift.modules.rotations.scenario_planning.explanation import evidence_pack

    for e in farm_data[2]:
        for row in e["seasons"]:
            row["harvest_basis"] = {"basis": "dry_matter", "moisture_fraction": 0.0}
            row["yield_t_ha"].update(
                status="available", value=3.0, lower=2.0, upper=5.0, reasons=[]
            )
    r, c, e = validated(farm_data)
    comparison = BoundedSelector().compare(r, c, e)
    pack = evidence_pack(comparison, comparison.alternatives[0].alternative_id, evaluations=e)
    import json

    facts = [json.loads(v) for k, v in pack["facts"].items() if k.startswith("evaluation-")]
    assert len(facts) == 3
    for fact in facts:
        assert fact["seasons"][0]["yield_t_ha"]["lower"] == 2.0
        assert fact["seasons"][0]["yield_t_ha"]["upper"] == 5.0
        assert fact["seasons"][0]["root_zone_reserve_mm"]["value"] is None
        assert fact["seasons"][0]["yield_t_ha"]["unit"] == "t/ha"


def test_model_yield_must_be_validated_and_basis_compatible(farm_data):
    for assumption in farm_data[0]["economics"]:
        assumption.update(yield_origin="model", user_yield_t_ha=None)
    for e in farm_data[2]:
        e["applicability"] = "validated"  # Test only; no real validation is asserted.
        for row in e["seasons"]:
            row["harvest_basis"] = {"basis": "dry_matter", "moisture_fraction": 0.0}
            row["yield_t_ha"].update(
                status="available", value=2.0, lower=1.0, upper=3.0, reasons=[]
            )
    result = BoundedSelector().compare(*validated(farm_data))
    assert result.alternatives[0].objective_value == 4200
    for e in farm_data[2]:
        e["applicability"] = "baseline"
    assert not BoundedSelector().compare(*validated(farm_data)).alternatives
    for e in farm_data[2]:
        e["applicability"] = "validated"
        for row in e["seasons"]:
            row["harvest_basis"] = {"basis": "fresh_weight", "moisture_fraction": 0.5}
    assert not BoundedSelector().compare(*validated(farm_data)).alternatives


def test_measured_zero_and_negative_margin_are_valid(farm_data):
    for assumption in farm_data[0]["economics"]:
        assumption["user_yield_t_ha"] = 0.0
    result = BoundedSelector().compare(*validated(farm_data))
    assert result.solver_status == "optimal"
    assert result.alternatives[0].objective_value == -1800


def test_evaluation_uses_pinned_weather_issue_not_later_submission_clock(farm_data):
    """B dates evidence at the weather issue; submitting later must preserve that date."""
    from datetime import timedelta

    from field_shift.modules.rotations.scenario_planning.selector import candidate_errors

    request, candidates, evaluations = validated(farm_data)
    later = request.model_copy(update={"as_of": request.as_of + timedelta(hours=1)})
    assert not candidate_errors(later, candidates[0], evaluations[0])
    future = evaluations[0].model_copy(update={"as_of": later.as_of + timedelta(seconds=1)})
    assert any(r.code == "evaluation_scope" for r in candidate_errors(later, candidates[0], future))


def test_runtime_capped_candidates_cannot_prove_infeasibility(farm_data, monkeypatch):
    from types import SimpleNamespace

    from field_shift.modules.planning_runtime.calendars import mark_search_coverage
    from field_shift.modules.rotations.suitability.engine import SuitabilityEngine

    farm_data[0]["resources"] = [
        dict(constraint_id="budget", season=2027, budget=2000.0, currency="USD", irrigation_m3=None)
    ]
    request, known, evidence = validated(farm_data)
    suitability = SuitabilityEngine(
        SimpleNamespace(version=request.rulebook_version, rules=[]), region_for=lambda _: "region"
    )
    monkeypatch.setattr(
        suitability,
        "evaluate",
        lambda field, crop, *args, **kwargs: next(
            c.assessments[0]
            for c in known
            if c.field_id == field.field_id and c.assignments[0].crop == crop
        ),
    )
    for cap in (1, 2):
        limited = request.model_copy(
            update={
                "solver_limits": request.solver_limits.model_copy(
                    update={"max_candidates_per_field": cap}
                )
            }
        )
        candidates = suitability.candidates(limited)
        evaluations = [
            next(
                e
                for e in evidence
                if e.field_snapshot_id == c.field_snapshot_id
                and e.seasons[0].crop == c.assignments[0].crop
            ).model_copy(update={"sequence_id": c.sequence_id})
            for c in candidates
        ]
        comparison = mark_search_coverage(
            BoundedSelector().compare(limited, candidates, evaluations), limited
        )
        if cap == 1:
            assert not comparison.alternatives
            assert comparison.solver_status == "limit_reached"
            assert comparison.diagnostics[0].code == "candidate_generation_limit"
        else:
            assert comparison.alternatives
            assert comparison.solver_status == "optimal"
