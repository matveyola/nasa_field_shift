"""Real PostgreSQL concurrency and migrations; fast SQLite lifecycle coverage."""

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from alembic import command
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from field_shift.contracts.planning_v1.planning import ApplyPlanRequest, ReplanRequest
from field_shift.modules.access.service import Principal
from field_shift.modules.fields.models import Field
from field_shift.modules.rotations.scenario_planning import service as clock_service
from field_shift.modules.rotations.scenario_planning.common import canonical, digest
from field_shift.modules.rotations.scenario_planning.models import (
    PlanningCommitment,
    PlanningRunRow,
)
from field_shift.modules.rotations.scenario_planning.selector import BoundedSelector
from field_shift.modules.rotations.scenario_planning.service import PlanningFailure, RunService

from .conftest import parse, validated

EDITOR = Principal(user_id=None, farm_id=1, name="Synthetic test editor", email=None, role="editor")
VIEWER = Principal(user_id=None, farm_id=1, name="Synthetic test viewer", email=None, role="viewer")


class SyntheticResolver:
    """Test-only trusted-snapshot adapter; never used by production."""

    def __init__(self):
        self.versions = dict(
            profiles="v1",
            geometry="v1",
            weather="v1",
            rulebook="v1",
            cultivars="v1",
            models="synthetic-v1",
        )

    def resolve(self, session, request, *, lock):
        return request.model_copy(deep=True), copy.deepcopy(self.versions)


def submit_complete(engine, farm_data, resolver=None):
    service = RunService(resolver or SyntheticResolver())
    request, candidates, evaluations = validated(farm_data)
    with Session(engine) as session:
        run = service.submit(session, EDITOR, request)
        session.commit()
    with Session(engine) as session:
        claimed = service.claim(session, run.run_id)
        session.commit()
    comparison = BoundedSelector().compare(request, candidates, evaluations)
    with Session(engine) as session:
        assert service.finish(
            session,
            run.run_id,
            claimed.attempt,
            comparison=comparison,
            artifacts={"synthetic": True},
        )
        session.commit()
    return service, run, comparison


def apply_payload(run, comparison, request_id="apply-1"):
    return ApplyPlanRequest(
        request_id=request_id,
        farm_id="1",
        run_id=run.run_id,
        alternative_id=comparison.alternatives[0].alternative_id,
        expected_input_sha256=run.input_sha256,
        expected_snapshots=comparison.snapshots,
        confirmed_by_human=True,
    )


def test_lifecycle_idempotency_permissions_and_stale(database, farm_data):
    engine, _, _ = database
    resolver = SyntheticResolver()
    service, run, comparison = submit_complete(engine, farm_data, resolver)
    request, _, _ = validated(farm_data)
    with Session(engine) as session:
        assert service.submit(session, EDITOR, request).run_id == run.run_id
        changed = request.model_copy(update={"economic_correlation_note": "changed assumptions"})
        with pytest.raises(PlanningFailure, match="different payload"):
            service.submit(session, EDITOR, changed)
        session.rollback()
        assert len(service.list(session, VIEWER, "1")) == 1
        with pytest.raises(PlanningFailure) as failure:
            service.apply(session, VIEWER, apply_payload(run, comparison))
        assert failure.value.code == "forbidden"
        session.rollback()
        resolver.versions["models"] = "changed-model"
        with pytest.raises(PlanningFailure) as failure:
            service.apply(session, EDITOR, apply_payload(run, comparison))
        assert failure.value.code == "stale"
        session.rollback()
        resolver.versions["models"] = "synthetic-v1"
        first = service.apply(session, EDITOR, apply_payload(run, comparison))
        session.commit()
        assert first.status == "applied"
        assert (
            service.apply(session, EDITOR, apply_payload(run, comparison)).status
            == "already_applied"
        )
        assert len(list(session.scalars(select(PlanningCommitment)))) == 3
        with pytest.raises(PlanningFailure):
            service.apply(session, EDITOR, apply_payload(run, comparison, "different-id"))
        session.rollback()
        with pytest.raises(PlanningFailure) as failure:
            service.detail(session, EDITOR, "other-farm", run.run_id)
        assert failure.value.code == "not_found"


def test_migration_downgrade_preserves_baseline(database):
    engine, config, baseline = database
    assert "planning_v1_runs" in inspect(engine).get_table_names()
    command.downgrade(config, "0010_precision_plans")
    assert "planning_v1_runs" not in inspect(engine).get_table_names()
    with Session(engine) as session:
        assert session.get(Field, "F-01").name == baseline
    command.upgrade(config, "0012_planning_runs")
    assert "planning_v1_runs" in inspect(engine).get_table_names()


def test_attempt_fence_recovery_and_cancellation(database, farm_data):
    engine, _, _ = database
    service = RunService(SyntheticResolver())
    request, candidates, evaluations = validated(farm_data)
    with Session(engine) as session:
        run = service.submit(session, EDITOR, request)
        session.commit()
        first = service.claim(session, run.run_id)
        session.commit()
        assert service.claim(session, run.run_id) is None
        row = session.get(PlanningRunRow, run.run_id)
        row.lease_until = clock_service.now() - timedelta(seconds=1)
        session.commit()
        second = service.claim(session, run.run_id)
        session.commit()
        assert second.attempt == first.attempt + 1
        comparison = BoundedSelector().compare(request, candidates, evaluations)
        assert not service.finish(session, run.run_id, first.attempt, comparison=comparison)
        assert service.cancel(session, EDITOR, "1", run.run_id).status == "cancelled"
        session.commit()
        assert not service.finish(session, run.run_id, second.attempt, comparison=comparison)
        assert service.claim(session, run.run_id) is None


@pytest.mark.integration
@pytest.mark.parametrize("database", ["postgres"], indirect=True)
def test_postgres_concurrent_apply_and_submit(database, farm_data):
    engine, _, _ = database
    service = RunService(SyntheticResolver())
    request, candidates, evaluations = validated(farm_data)
    barrier = Barrier(2)

    def submit():
        barrier.wait(timeout=10)
        with Session(engine) as session:
            run = service.submit(session, EDITOR, request)
            session.commit()
            return run.run_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: submit(), range(2)))
    assert ids[0] == ids[1]
    with Session(engine) as session:
        run = service.claim(session, ids[0])
        session.commit()
        comparison = BoundedSelector().compare(request, candidates, evaluations)
        assert service.finish(session, run.run_id, run.attempt, comparison=comparison)
        session.commit()
    payload = apply_payload(run, comparison)
    barrier = Barrier(2)

    def apply():
        barrier.wait(timeout=10)
        with Session(engine) as session:
            result = service.apply(session, EDITOR, payload)
            session.commit()
            return result.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: apply(), range(2)))
    assert sorted(outcomes) == ["already_applied", "applied"]


def test_replan_requires_commitments_and_new_request(database, farm_data):
    engine, _, _ = database
    service, run, comparison = submit_complete(engine, farm_data)
    with Session(engine) as session:
        service.apply(session, EDITOR, apply_payload(run, comparison))
        session.commit()
        new = copy.deepcopy(farm_data[0])
        new["request_id"] = "replan-2"
        from field_shift.contracts.planning_v1 import FarmPlanningRequest

        payload = ReplanRequest(
            previous_run_id=run.run_id,
            request=parse(FarmPlanningRequest, new),
            preserve_commitment_ids=[],
        )
        with pytest.raises(PlanningFailure):
            service.replan(session, EDITOR, payload)
        session.rollback()
        commitments = list(session.scalars(select(PlanningCommitment)))
        new["locks"] = [c.assignment for c in commitments]
        payload = ReplanRequest(
            previous_run_id=run.run_id,
            request=parse(FarmPlanningRequest, new),
            preserve_commitment_ids=[c.commitment_id for c in commitments],
        )
        # Applied commitments, as well as original locks, must survive replan.
        new_run = service.replan(session, EDITOR, payload)
        session.commit()
        assert new_run.run_id != run.run_id and new_run.status == "queued"
        assert digest(new_run.request) == new_run.input_sha256
        assert canonical(service.detail(session, EDITOR, "1", run.run_id).request) == canonical(
            run.request
        )


def test_three_expired_leases_are_terminal(database, farm_data):
    engine, _, _ = database
    service = RunService(SyntheticResolver())
    request, _, _ = validated(farm_data)
    with Session(engine) as session:
        run = service.submit(session, EDITOR, request)
        session.commit()
        for attempt in range(1, 4):
            claimed = service.claim(session, run.run_id)
            assert claimed.attempt == attempt
            session.commit()
            session.get(PlanningRunRow, run.run_id).lease_until = clock_service.now() - timedelta(
                seconds=1
            )
            session.commit()
        assert service.claim(session, run.run_id) is None
        session.commit()
        failed = service.detail(session, VIEWER, "1", run.run_id)
        assert failed.status == "failed" and failed.error.code == "attempts_exhausted"


@pytest.mark.parametrize("version", ["profiles", "geometry", "weather", "rulebook", "cultivars"])
def test_current_version_changes_invalidate_apply(database, farm_data, version):
    engine, _, _ = database
    resolver = SyntheticResolver()
    service, run, comparison = submit_complete(engine, farm_data, resolver)
    resolver.versions[version] = "updated"
    with Session(engine) as session:
        with pytest.raises(PlanningFailure) as failure:
            service.apply(session, EDITOR, apply_payload(run, comparison))
        assert failure.value.code == "stale"
        session.rollback()
        assert not list(session.scalars(select(PlanningCommitment)))


def test_expired_weather_cannot_apply(database, farm_data, monkeypatch):
    from datetime import UTC, datetime

    from field_shift.modules.rotations.scenario_planning import service as service_module

    engine, _, _ = database
    service, run, comparison = submit_complete(engine, farm_data)
    monkeypatch.setattr(service_module, "now", lambda: datetime(2026, 12, 2, tzinfo=UTC))
    with Session(engine) as session:
        with pytest.raises(PlanningFailure) as failure:
            service.apply(session, EDITOR, apply_payload(run, comparison))
        assert failure.value.code == "stale"
