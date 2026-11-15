"""Legacy result readability is separate from permission for a NEW application."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from field_shift_contracts.planning_v1 import FarmPlanComparison
from field_shift_contracts.planning_v1.planning import ApplyPlanRequest, ApplyPlanResult
from field_shift_science.fields.land import LAND_ELIGIBILITY_POLICY_VERSION
from sqlalchemy import select

from field_shift.modules.planning_runtime.inputs import install_bundle
from field_shift.modules.planning_runtime.service import service
from field_shift.modules.rotations.scenario_planning.common import digest
from field_shift.modules.rotations.scenario_planning.models import (
    PlanningApplyRow,
    PlanningCommitment,
    PlanningRunRow,
)
from field_shift.modules.rotations.scenario_planning.service import PlanningFailure

from .conftest import ADMIN


def test_old_policy_result_requires_replan_but_history_and_applied_replay_survive(
    factory,
    installed_inputs,
    example,
):
    bundle, _ = installed_inputs
    # Real profile repository/trusted resolver fixture, deliberately legacy unbound
    # profile. Construct an explicitly synthetic old succeeded outcome, not a new
    # engine recommendation or observed result.
    assert bundle.template.fields[0].land_source_ids == []
    with factory.begin() as session:
        install_bundle(session, bundle, ADMIN, service)
        run = service.submit(session, ADMIN, bundle.template)
        row = session.get(PlanningRunRow, run.run_id)
        assert row.versions["eligibility_policy"] == LAND_ELIGIBILITY_POLICY_VERSION
        old_versions = {k: v for k, v in row.versions.items() if k != "eligibility_policy"}
        claimed = service.claim(session, run.run_id)
        profile = run.request.fields[0]
        assignment = {
            "field_id": profile.field_id,
            "season": run.request.start_season,
            "crop": run.request.enabled_crops[0].model_dump(mode="json"),
            "sowing_window": None,
            "harvest_window": None,
            "locked": False,
            "commitment_id": None,
        }
        document = dict(
            example["comparison"],
            request_id=run.request.request_id,
            farm_id="1",
            input_sha256=run.input_sha256,
            solver_status="feasible",
            snapshots=[
                {
                    "field_id": profile.field_id,
                    "snapshot_id": profile.snapshot_id,
                    "profile_revision": profile.profile_revision,
                    "geometry_revision": profile.geometry_revision,
                }
            ],
            alternatives=[
                {
                    "alternative_id": "legacy-controlled",
                    "label": "Synthetic old outcome",
                    "assignments": [assignment],
                    "sequence_ids": ["old-sequence"],
                    "evaluation_ids": [],
                    "scenario_results": [],
                    "objective_value": None,
                    "objective_unit": "mm_ha",
                    "reasons": example["comparison"]["diagnostics"],
                }
            ],
        )
        import json

        comparison = FarmPlanComparison.model_validate_json(json.dumps(document))
        assert service.finish(
            session,
            run.run_id,
            claimed.attempt,
            comparison=comparison,
            artifacts={"synthetic_legacy_result": True},
        )
        row.versions = old_versions
    payload = ApplyPlanRequest(
        request_id="legacy-apply",
        farm_id="1",
        run_id=run.run_id,
        alternative_id="legacy-controlled",
        expected_input_sha256=run.input_sha256,
        expected_snapshots=comparison.snapshots,
        confirmed_by_human=True,
    )
    with factory() as session:
        read = service.detail(session, ADMIN, "1", run.run_id)
        assert read.status == "succeeded" and read.input_sha256 == run.input_sha256
        assert service.list(session, ADMIN, "1")[0].run_id == run.run_id
        with pytest.raises(PlanningFailure, match="replan") as error:
            service.apply(session, ADMIN, payload)
        assert error.value.code == "stale"
        session.rollback()
        assert not list(session.scalars(select(PlanningCommitment)))
    # Historical already-applied records retain their immutable receipt and crops.
    with factory.begin() as session:
        commitment = dict(assignment, locked=True, commitment_id="old-commitment")
        session.add(
            PlanningCommitment(
                farm_id=1,
                field_id=profile.field_id,
                season=run.request.start_season,
                commitment_id="old-commitment",
                assignment=commitment,
            )
        )
        receipt = ApplyPlanResult(
            request_id=payload.request_id,
            status="applied",
            applied_at=datetime(2026, 11, 14, tzinfo=UTC),
            reasons=[],
        )
        session.add(
            PlanningApplyRow(
                id=str(uuid4()),
                farm_id=1,
                run_id=run.run_id,
                request_id=payload.request_id,
                input_hash=digest(payload),
                alternative_id=payload.alternative_id,
                result=receipt.model_dump(mode="json"),
            )
        )
    with factory.begin() as session:
        replay = service.apply(session, ADMIN, payload)
        assert replay.status == "already_applied" and replay.applied_at == receipt.applied_at
        assert session.scalar(select(PlanningCommitment)).assignment == commitment
        assert session.get(PlanningRunRow, run.run_id).versions == old_versions
