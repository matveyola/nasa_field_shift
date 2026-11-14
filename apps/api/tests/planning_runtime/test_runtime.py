from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from field_shift.api import create_app
from field_shift.config import Settings
from field_shift.jobs.planning import dispatch, process_planning
from field_shift.modules.access.dependencies import require_principal
from field_shift.modules.planning_runtime.inputs import install_bundle
from field_shift.modules.planning_runtime.models import PlanningDelivery
from field_shift.modules.planning_runtime.service import service
from field_shift.modules.rotations.scenario_planning.models import PlanningRunRow
from field_shift.modules.rotations.scenario_planning.service import PlanningFailure

from .conftest import ADMIN, VIEWER


def install(factory, inputs):
    with factory.begin() as session:
        return install_bundle(session, inputs, ADMIN, service)


def submit(factory, inputs):
    with factory.begin() as session:
        return service.submit(session, ADMIN, inputs.template)


def test_profile_bootstrap_and_mounted_farm_scoped_routes(factory, installed_inputs):
    bundle, _ = installed_inputs
    app = create_app(Settings(environment="test"), session_factory=factory)
    with TestClient(app) as client:
        boot = client.get("/api/planning-workspace")
        assert boot.status_code == 200 and boot.json()["request"] is None
        assert boot.json()["profiles"][0]["field_id"] == "F-01"
        install(factory, bundle)
        boot = client.get("/api/planning-workspace").json()
        assert boot["status"] == "synthetic" and boot["request"] is not None
        result = client.post(
            "/api/planning-v1/farms/1/runs", json=bundle.template.model_dump(mode="json")
        )
        assert result.status_code == 202, result.text
        run = result.json()
        assert (
            client.get(
                "/api/planning-workspace/farms/2/runs/" + run["run_id"] + "/evidence"
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/api/planning-workspace/farms/1/runs/" + run["run_id"] + "/evidence"
            ).json()["evaluations"]
            == []
        )
        app.dependency_overrides[require_principal] = lambda: VIEWER
        assert (
            client.post(
                "/api/planning-v1/farms/1/runs", json=bundle.template.model_dump(mode="json")
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/planning-workspace/farms/1/runs/" + run["run_id"] + "/cancel"
            ).status_code
            == 403
        )
        app.dependency_overrides[require_principal] = lambda: ADMIN
        assert (
            client.post("/api/planning-workspace/farms/1/runs/" + run["run_id"] + "/cancel").json()[
                "status"
            ]
            == "cancelled"
        )


def test_source_tampering_and_profile_edits_invalidate_admission(factory, installed_inputs):
    bundle, _ = installed_inputs
    install(factory, bundle)
    request = bundle.template.model_copy(deep=True)
    request.weather.scenarios[0].traces[0].days[0].liquid_rain_mm = 987
    with factory.begin() as session, pytest.raises(PlanningFailure, match="Weather"):
        service.submit(session, ADMIN, request)
    from field_shift.modules.fields.models import Field

    with factory.begin() as session:
        session.get(Field, "F-01").boundary_revision += 1
    with factory.begin() as session, pytest.raises(PlanningFailure, match="Boundary"):
        service.submit(session, ADMIN, bundle.template)


def test_atomic_delivery_duplicate_publish_failure_and_recovery(factory, installed_inputs):
    bundle, _ = installed_inputs
    install(factory, bundle)
    with factory() as session:
        run = service.submit(session, ADMIN, bundle.template)
        session.rollback()
    with factory() as session:
        assert session.get(PlanningRunRow, run.run_id) is None
        assert session.get(PlanningDelivery, run.run_id) is None
    run = submit(factory, bundle)
    assert submit(factory, bundle).run_id == run.run_id
    timestamp = datetime.now(UTC)

    def down(_identifier):
        raise ConnectionError("controlled broker outage")

    dispatch(factory, down, timestamp=timestamp)
    ids = []
    assert dispatch(factory, ids.append, timestamp=timestamp + timedelta(seconds=1)) == 0
    assert dispatch(factory, ids.append, timestamp=timestamp + timedelta(seconds=31)) == 1
    assert ids == [run.run_id]
    with factory() as session:
        assert len(list(session.scalars(select(PlanningDelivery)))) == 1


def test_model_failure_is_terminal_and_cancelled_attempt_cannot_publish(factory, installed_inputs):
    bundle, _ = installed_inputs
    install(factory, bundle)
    run = submit(factory, bundle)

    def failed(_factory, _run):
        raise TimeoutError("controlled model timeout")

    process_planning(factory, run.run_id, runner=failed)
    with factory() as session:
        row = session.get(PlanningRunRow, run.run_id)
        assert row.status == "failed" and row.artifacts is None
    request = bundle.template.model_copy(update={"request_id": str(uuid4())})
    with factory.begin() as session:
        second = service.submit(session, ADMIN, request)

    def cancel(_factory, current):
        with factory.begin() as session:
            service.cancel(session, ADMIN, "1", current.run_id)
        raise InterruptedError("cancelled")

    process_planning(factory, second.run_id, runner=cancel)
    with factory() as session:
        assert session.get(PlanningRunRow, second.run_id).status == "cancelled"


def test_unreviewed_inputs_are_not_a_production_bundle(installed_inputs):
    from field_shift.modules.planning_runtime.inputs import InputBundle

    bundle, _ = installed_inputs
    raw = bundle.model_dump(mode="json")
    raw["synthetic"] = False
    import json

    with pytest.raises(ValueError, match="Production inputs"):
        InputBundle.model_validate_json(json.dumps(raw))


def test_missing_configuration_never_produces_mock_request(factory):
    with factory.begin() as session:
        boot = service.bootstrap(session, ADMIN)
    assert boot["request"] is None and boot["model_card"] is None


def test_failed_model_gate_is_preserved(factory, installed_inputs, monkeypatch):
    from field_shift.modules.planning_runtime.transport import ModelClient

    bundle, card = installed_inputs
    monkeypatch.setattr(
        ModelClient, "card", lambda *_: card.model_copy(update={"promotion": "failed"})
    )
    with factory.begin() as session, pytest.raises(PlanningFailure, match="release gate"):
        install_bundle(session, bundle, ADMIN, service)


def test_changed_model_ref_requires_new_configuration(factory, installed_inputs, monkeypatch):
    from field_shift.modules.planning_runtime.transport import ModelClient

    bundle, card = installed_inputs
    install(factory, bundle)
    changed = card.model_copy(deep=True)
    changed.model.artifact_sha256 = "f" * 64
    monkeypatch.setattr(ModelClient, "card", lambda *_: changed)
    with factory.begin() as session, pytest.raises(PlanningFailure, match="Model artifact changed"):
        service.submit(session, ADMIN, bundle.template)


def test_profile_body_is_bounded_and_encoded_inputs_rejected(factory):
    app = create_app(Settings(environment="test"), session_factory=factory)
    with TestClient(app) as client:
        path = "/api/planning-v1/farms/1/fields/F-01/profile"
        assert client.put(path, content=b"x" * 8_000_001).status_code == 413
        assert (
            client.put(path, content=b"{}", headers={"Content-Encoding": "gzip"}).status_code == 415
        )


def test_expired_lease_is_republished_but_old_attempt_cannot_finish(factory, installed_inputs):
    bundle, _ = installed_inputs
    install(factory, bundle)
    run = submit(factory, bundle)
    with factory.begin() as session:
        first = service.claim(session, run.run_id)
        row = session.get(PlanningRunRow, run.run_id)
        row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    published = []
    assert dispatch(factory, published.append) == 1
    assert published == [run.run_id]
    with factory.begin() as session:
        second = service.claim(session, run.run_id)
        assert second.attempt == first.attempt + 1
        service.finish(session, run.run_id, first.attempt, error=None)
    with factory() as session:
        row = session.get(PlanningRunRow, run.run_id)
        assert row.status == "running" and row.attempt == second.attempt


def test_calendars_only_narrow_permitted_dates_and_preserve_commitments(example, monkeypatch):
    import json
    from datetime import date
    from types import SimpleNamespace

    from field_shift.contracts.planning_v1 import CandidateSequence
    from field_shift.contracts.planning_v1.common import DateWindow
    from field_shift.modules.planning_runtime.calendars import Calendar, CalendarSuitability
    from field_shift.modules.rotations.suitability.engine import SuitabilityEngine

    candidate = CandidateSequence.model_validate_json(json.dumps(example["candidate"]))
    assignment = candidate.assignments[0]
    # The frozen wire example has unknown dates. This case explicitly supplies
    # reviewed windows to exercise narrowing, rather than inventing them at runtime.
    assignment.locked = False
    assignment.sowing_window = DateWindow(
        start=date(assignment.season, 4, 1), end=date(assignment.season, 4, 30)
    )
    assignment.harvest_window = DateWindow(
        start=date(assignment.season, 9, 1), end=date(assignment.season, 9, 30)
    )
    calendar = Calendar(
        field_id=assignment.field_id,
        crop=assignment.crop,
        season=assignment.season,
        sowing_date=assignment.sowing_window.start,
        harvest_date=assignment.harvest_window.end,
        source="explicit-test-calendar",
        available_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    monkeypatch.setattr(SuitabilityEngine, "candidates", lambda *_: [candidate])
    adapter = object.__new__(CalendarSuitability)
    adapter.calendars = [calendar]
    request = SimpleNamespace(as_of=datetime(2026, 11, 14, tzinfo=UTC))
    adapter.candidates(request)
    assert assignment.sowing_window.start == assignment.sowing_window.end
    assert assignment.harvest_window.start == assignment.harvest_window.end
    original = assignment.model_copy(deep=True)
    assignment.locked = True
    calendar.sowing_date = calendar.harvest_date + timedelta(days=1)
    adapter.candidates(request)
    assert assignment.sowing_window == original.sowing_window
    assignment.locked = False
    adapter.candidates(request)
    assert candidate.assessments[0].status == "ineligible"
    assert candidate.assessments[0].reasons[-1].code == "calendar_outside_rules"


@pytest.mark.parametrize("defect", ["hash", "request", "farm", "weather", "rules", "artifacts"])
def test_rejected_worker_results_finish_as_failed(factory, installed_inputs, example, defect):
    import copy

    bundle, _ = installed_inputs
    install(factory, bundle)
    run = submit(factory, bundle)
    comparison = copy.deepcopy(example["comparison"])
    comparison.update(
        request_id=run.request.request_id,
        farm_id=run.request.farm_id,
        input_sha256=run.input_sha256,
        scenario_set_id=run.request.weather.scenario_set_id,
        rulebook_version=run.request.rulebook_version,
    )
    keys = {
        "hash": "input_sha256",
        "request": "request_id",
        "farm": "farm_id",
        "weather": "scenario_set_id",
        "rules": "rulebook_version",
    }
    if defect in keys:
        comparison[keys[defect]] = "f" * 64 if defect == "hash" else "other"
    payload = {"comparison": comparison, "artifacts": {}}
    if defect == "artifacts":
        del payload["artifacts"]
    process_planning(factory, run.run_id, runner=lambda *_: payload)
    with factory() as session:
        row = session.get(PlanningRunRow, run.run_id)
        assert row.status == "failed"
        assert row.payload["comparison"] is None and row.payload["error"]
        assert row.artifacts is None and row.lease_until is None


def test_boundary_refresh_requires_explicit_action_and_archives_old_profile(
    factory, installed_inputs
):
    from field_shift.modules.fields.models import Field
    from field_shift.modules.fields.planning_profile.service import ProfileRepository

    bundle, _ = installed_inputs
    old = bundle.template.fields[0]
    with factory.begin() as session:
        session.get(Field, "F-01").boundary_revision += 1
    with factory.begin() as session, pytest.raises(PlanningFailure, match="Boundary"):
        service.initialize_profile(session, ADMIN, "F-01")
    with factory.begin() as session:
        fresh = service.initialize_profile(session, ADMIN, "F-01", refresh_boundary=True)
        assert fresh.geometry_revision == old.geometry_revision + 1
        assert fresh.profile_revision == old.profile_revision + 1
        assert fresh.land_status == "unknown" and fresh.soil == [] and fresh.water_state is None
        assert fresh.missing_data
    with factory.begin() as session:
        assert ProfileRepository(session).get_snapshot("1", "F-01", old.snapshot_id) == old
        assert service.initialize_profile(session, ADMIN, "F-01", refresh_boundary=True) == fresh
