"""Injected boundary tests; all adapters below are explicitly synthetic."""

import copy
import json

import pytest
from pydantic import ValidationError

from field_shift.contracts.planning_v1 import FarmPlanningRequest, ModelCard
from field_shift.modules.rotations.scenario_planning.common import digest
from field_shift.modules.rotations.scenario_planning.execution import PlanningEngine
from field_shift.modules.rotations.scenario_planning.selector import BoundedSelector

from .conftest import ROOT, parse, validated


class SyntheticAdapters:
    def __init__(self, farm_data):
        self.request, self.sequences, self.evaluations = validated(farm_data)
        x = json.loads(
            (ROOT / "contracts/planning-v1/examples/planning-contracts.json").read_text()
        )
        x["model_card"]["capabilities"] = ["yield", "root_zone_water"]
        x["model_card"]["supported_crops"] = farm_data[0]["enabled_crops"]
        self.card = parse(ModelCard, x["model_card"])

    def get_snapshot(self, farm_id, field_id, snapshot_id):
        return next(f for f in self.request.fields if f.snapshot_id == snapshot_id)

    def candidates(self, request):
        return self.sequences

    def model_card(self):
        return self.card

    def evaluate(self, profile, candidate, weather):
        return next(e for e in self.evaluations if e.sequence_id == candidate.sequence_id)


def test_orchestration_retains_pinned_artifacts(farm_data):
    adapter = SyntheticAdapters(farm_data)
    result, artifacts = PlanningEngine(adapter, adapter, adapter).execute(adapter.request)
    assert result.input_sha256 == digest(adapter.request)
    assert result.alternatives[0].objective_value == 12000
    assert artifacts["model_card"]["promotion"] == "insufficient_evidence"
    assert all(e["applicability"] == "baseline" for e in artifacts["evaluations"])
    assert artifacts.pop("sha256") == digest(artifacts)


def test_unpromoted_model_cannot_claim_validated(farm_data):
    adapter = SyntheticAdapters(farm_data)
    adapter.evaluations = [
        e.model_copy(update={"applicability": "validated"}) for e in adapter.evaluations
    ]
    with pytest.raises(ValueError, match="unpromoted"):
        PlanningEngine(adapter, adapter, adapter).execute(adapter.request)


def test_wrong_artifact_rejected(farm_data):
    adapter = SyntheticAdapters(farm_data)
    adapter.evaluations[0].model = adapter.evaluations[0].model.model_copy(
        update={"version": "other"}
    )
    with pytest.raises(ValueError, match="pinned"):
        PlanningEngine(adapter, adapter, adapter).execute(adapter.request)


def test_three_year_versions_and_state_continuity(farm_data):
    r, sequences, evaluations = farm_data
    r["season_count"] = 3
    for field in r["fields"]:
        field["management"]["irrigation"] = [
            dict(field["management"]["irrigation"][0], season=s) for s in (2027, 2028, 2029)
        ]
    r["economics"] = [
        dict(e, season=s, assumption_id=e["assumption_id"] + str(s))
        for e in r["economics"]
        for s in (2027, 2028, 2029)
    ]
    for c in sequences:
        c["assignments"] = [dict(c["assignments"][0], season=s) for s in (2027, 2028, 2029)]
        c["assessments"] = [dict(c["assessments"][0], season=s) for s in (2027, 2028, 2029)]
    for e in evaluations:
        e["seasons"] = [
            dict(row, season=s, opening_state_id=f"state-{s}", closing_state_id=f"state-{s + 1}")
            for row in e["seasons"]
            for s in (2027, 2028, 2029)
        ]
    request, candidates, outputs = validated(farm_data)
    result = BoundedSelector().compare(request, candidates, outputs)
    assert result.alternatives[0].objective_value == 36000
    assert len(result.alternatives[0].assignments) == 9
    assert len(result.alternatives[0].scenario_results) == 6
    broken = copy.deepcopy(farm_data)
    broken[2][0]["seasons"][1]["opening_state_id"] = "unrelated"
    with pytest.raises(ValidationError, match="carried state"):
        validated(broken)


@pytest.mark.parametrize(
    "case,mutation",
    [
        ("G18", "future_source"),
        ("G21", "mixed_currency"),
        ("G18", "future_economics"),
        ("G21", "missing_currency"),
        ("G24", "uncalibrated_weights"),
    ],
)
def test_frozen_boundary_rejects_invalid_inputs(farm_data, case, mutation):
    r = farm_data[0]
    if mutation == "future_source":
        r["fields"][0]["sources"][0]["available_at"] = "2026-11-15T00:00:00Z"
        r["fields"][0]["sources"][0]["retrieved_at"] = "2026-11-15T00:00:00Z"
    elif mutation == "mixed_currency":
        r["economics"][0]["currency"] = "EUR"
    elif mutation == "future_economics":
        r["economics"][0]["available_at"] = "2026-11-15T00:00:00Z"
    elif mutation == "missing_currency":
        r["economics"][0]["currency"] = None
    else:
        r["weather"]["scenarios"][0]["weight"] = 0.5
    with pytest.raises(ValidationError):
        parse(FarmPlanningRequest, r)


@pytest.mark.parametrize(
    "case,status",
    [
        ("G07", "insufficient_data"),
        ("G08", "unsupported"),
        ("G09", "unsupported"),
        ("G10", "ineligible"),
        ("G11", "unsupported"),
        ("G19", "out_of_domain"),
        ("G20", "unsupported"),
        ("G22", "unsupported"),
    ],
)
def test_upstream_domain_abstentions_are_preserved(farm_data, case, status):
    # These verify C consumes A/B outcomes; they do not execute A/B physical predicates.
    for c in farm_data[1]:
        c["assessments"][0]["status"] = status
        c["assessments"][0]["reasons"][0].update(
            code=f"{case}-synthetic-rule",
            message=f"Synthetic {case} applicability constraint, no empirical claim.",
        )
    result = BoundedSelector().compare(*validated(farm_data))
    assert not result.alternatives
    assert any(d.code == f"{case}-synthetic-rule" for d in result.diagnostics)
    assert result.solver_status == ("infeasible" if status == "ineligible" else "not_run")


def test_release_evidence_cannot_look_ahead(farm_data):
    from datetime import UTC, datetime

    adapter = SyntheticAdapters(farm_data)
    adapter.card = adapter.card.model_copy(
        update={"gate_frozen_at": datetime(2026, 11, 15, tzinfo=UTC)}
    )
    with pytest.raises(ValueError, match="issue time"):
        PlanningEngine(adapter, adapter, adapter).execute(adapter.request)
