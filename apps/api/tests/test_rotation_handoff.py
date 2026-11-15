import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from field_shift.db import build_session_factory
from field_shift.jobs import repository
from field_shift.jobs.models import Job, JobStatus
from field_shift.jobs.worker import InvalidModelInput
from field_shift.models import metadata
from field_shift.modules.access.service import Principal
from field_shift.modules.farm.models import Farm
from field_shift.modules.fields.models import Field
from field_shift.modules.rotations.handoff.schemas import HandoffSubmission
from field_shift.modules.rotations.handoff.service import catalog, read, submit
from field_shift.modules.rotations.handoff.transport import HandoffUnavailable
from field_shift.modules.rotations.handoff.worker import checkpoint, current_field, succeed

OWNER = Principal(None, 1, "test", None, "admin")
VIEWER = Principal(None, 1, "test", None, "viewer")


@pytest.fixture
def factory():
    source = os.getenv("FIELD_SHIFT_HANDOFF_TEST_DATABASE_URL")
    engine, schema = None, None
    if source:
        url = make_url(source)
        assert url.get_backend_name() == "postgresql"
        schema = "handoff_" + uuid4().hex
        engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = url.update_query_dict({"options": f"-csearch_path={schema}"})
        factory = build_session_factory(url.render_as_string(hide_password=False))
    else:
        factory = build_session_factory("sqlite://")
    metadata.create_all(factory.kw["bind"])
    with factory.begin() as session:
        session.add_all(
            [
                Farm(
                    id=i,
                    name=f"Farm{i}",
                    season=2026,
                    next_field_number=2,
                    irrigation_available=False,
                    priority="water",
                )
                for i in (1, 2)
            ]
        )
        session.flush()
        for key, farm in (("F-01", 1), ("OTHER", 2)):
            session.add(
                Field(
                    id=key,
                    farm_id=farm,
                    name="Actual registered geometry unit fixture",
                    crop="Maize",
                    season=2026,
                    area_ha=1,
                    boundary_revision=2,
                    geometry={
                        "type": "Polygon",
                        "coordinates": [
                            [[30, 50], [30.01, 50], [30.01, 50.01], [30, 50.01], [30, 50]]
                        ],
                    },
                )
            )
    yield factory
    factory.kw["bind"].dispose()
    if engine:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def body(**changes):
    return HandoffSubmission(
        request_id="one", geometry_revision=2, operation="forecast", parameters={}, **changes
    )


def queued(factory):
    with factory.begin() as session:
        return submit(session, OWNER, "F-01", body())


def test_submit_is_durable_idempotent_and_binds_owned_geometry(factory):
    first = queued(factory)
    with factory.begin() as session:
        second = submit(session, OWNER, "F-01", body())
        assert second.job_id == first.job_id
        job = session.get(Job, first.job_id)
        assert job.kind == "simulation" and job.status == "queued" and job.attempts == 0
        assert job.inputs["parameters"]["field"]["latitude"] == pytest.approx(50.005)
        assert not job.inputs["allow_public_data"]
        changed = body(allow_public_data=True)
        with pytest.raises(HTTPException) as conflict:
            submit(session, OWNER, "F-01", changed)
        assert conflict.value.status_code == 409
    with factory() as session:
        assert len(list(session.scalars(select(Job)))) == 1


@pytest.mark.parametrize(
    "principal,field_id,status", [(VIEWER, "F-01", 403), (OWNER, "OTHER", 404)]
)
def test_submission_scope_and_role(factory, principal, field_id, status):
    with factory() as session, pytest.raises(HTTPException) as error:
        submit(session, principal, field_id, body())
    assert error.value.status_code == status


def test_poll_cannot_read_another_field_job(factory):
    first = queued(factory)
    with factory() as session, pytest.raises(HTTPException) as error:
        read(session, OWNER, "OTHER", first.job_id)
    assert error.value.status_code == 404


def test_reference_scenario_cannot_masquerade_as_user_field(factory):
    with factory() as session, pytest.raises(HTTPException) as error:
        submit(
            session,
            OWNER,
            "F-01",
            HandoffSubmission(request_id="ref", geometry_revision=2, operation="sutherland_2027"),
        )
    assert error.value.status_code == 422


def test_acquisition_checkpoint_survives_recovery_without_mutating_request(factory):
    first = queued(factory)
    now = datetime.now(UTC)
    with factory.begin() as session:
        lease = repository.claim(session, first.job_id, lease_seconds=135, now=now)
    checkpoint(factory, lease, {"immutable": "input"})
    with factory.begin() as session:
        original = session.get(Job, first.job_id).inputs
        assert repository.recover_expired(session, now=now + timedelta(seconds=136)) == 1
        replacement = repository.claim(
            session, first.job_id, lease_seconds=135, now=now + timedelta(seconds=140)
        )
        assert replacement is not None
        assert session.get(Job, first.job_id).result["evidence"] == {"immutable": "input"}
        assert replacement.inputs == original
    with pytest.raises(InvalidModelInput):
        checkpoint(factory, lease, {"bad": "late"})


def test_boundary_change_between_execution_and_final_write_rejects_result(factory):
    first = queued(factory)
    now = datetime.now(UTC)
    with factory.begin() as session:
        lease = repository.claim(session, first.job_id, lease_seconds=135, now=now)
    with factory() as session:
        current_field(session, lease)
    with factory.begin() as session:
        session.get(Field, "F-01").boundary_revision = 3
    with factory.begin() as session:
        assert succeed(
            session, lease, {"envelope": {"bad": "stale"}}, now=now + timedelta(seconds=1)
        )
    with factory() as session:
        job = session.get(Job, first.job_id)
        assert job.status == JobStatus.FAILED and job.result is None


def test_expired_attempt_cannot_replace_current_result(factory):
    first = queued(factory)
    now = datetime.now(UTC)
    with factory.begin() as session:
        old = repository.claim(session, first.job_id, lease_seconds=1, now=now)
    with factory.begin() as session:
        repository.recover_expired(session, now=now + timedelta(seconds=2))
        fresh = repository.claim(
            session, first.job_id, lease_seconds=135, now=now + timedelta(seconds=5)
        )
        assert succeed(session, fresh, {"winner": "fresh"}, now=now + timedelta(seconds=6))
    with factory.begin() as session:
        assert not succeed(session, old, {"winner": "old"}, now=now + timedelta(seconds=7))
        assert session.get(Job, first.job_id).result == {"winner": "fresh"}


def test_catalog_keeps_thirteen_real_profiles_visible_during_service_outage():
    class Missing:
        def call(self, *args):
            raise HandoffUnavailable("Service offline")

    result = catalog(Missing())
    assert result.status == "unavailable" and len(result.crops) == 13
    assert len([r for r in result.crops if r.climate_supported]) == 7
    assert result.empirical_status == "not_validated"
    rapeseed = next(r for r in result.crops if r.id == "rapeseed_canola")
    assert rapeseed.profile["calendars"] == []


def test_http_auth_body_bound_and_durable_response(factory):
    from fastapi.testclient import TestClient

    from field_shift.api import create_app
    from field_shift.config import Settings
    from field_shift.modules.rotations.handoff.routes import router

    app = create_app(Settings(), session_factory=factory)
    app.include_router(router, prefix="/api")
    with TestClient(app) as client:
        assert (
            client.post("/api/rotations/handoff/fields/F-01/jobs", content="x" * 70001).status_code
            == 413
        )
        response = client.post(
            "/api/rotations/handoff/fields/F-01/jobs", json=body().model_dump(mode="json")
        )
        assert response.status_code == 202, response.text
        job = response.json()
        assert job["status"] == "queued" and job["result"] is None
        assert client.get(f"/api/rotations/handoff/fields/F-01/jobs/{job['job_id']}").json() == job
        assert client.get("/api/rotations/handoff/fields/OTHER/seasonal-water").status_code == 404
    app = create_app(Settings(access_mode="team"), session_factory=factory)
    app.include_router(router, prefix="/api")
    with TestClient(app) as client:
        assert client.get("/api/rotations/handoff/catalog").status_code == 401
        assert (
            client.post(
                "/api/rotations/handoff/fields/F-01/jobs",
                json=body().model_dump(mode="json"),
                headers={"X-Field-Shift-Request": "1"},
            ).status_code
            == 401
        )


def test_seasonal_rows_preserve_nulls_overlap_and_current_field_scope(factory):
    from field_shift.modules.rotations.handoff.water import seasonal_water

    first = queued(factory)
    with factory.begin() as session:
        job = session.get(Job, first.job_id)
        job.status = "succeeded"
        job.result = {
            "envelope": {
                "operation": "forecast",
                "data_kind": "hybrid_scenario",
                "evidence_sha256": "a" * 64,
                "payload": {
                    "plans": [
                        {
                            "id": "plan-a",
                            "members": [
                                {
                                    "historical_anchor_date": "2020-05-01",
                                    "seasons": [
                                        {
                                            "crop_id": "maize_grain",
                                            "planting_date": "2027-05-01",
                                            "through_date": "2027-09-27",
                                            "demand_mm": 100,
                                            "modeled_use_mm": 80,
                                            "deficit_mm": 20,
                                            "water_shortfall_pct": 19.98,
                                            "drought_stress_days": 5,
                                            "crop_days": 150,
                                            "water_stress_days": 6,
                                            "water_management": {"waterlogging_stress_days": 3},
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                },
            }
        }
    with factory() as session:
        data = seasonal_water(session, OWNER, "F-01", 20)
        assert len(data.rows) == 1
        row = data.rows[0]
        assert row.drought_days == 5 and row.waterlogging_days == 3 and row.water_stress_days == 6
        assert row.water_shortfall_pct == 19.98
        assert row.irrigation_mm is None and row.drainage_mm is None and row.opening_mm is None
    with factory.begin() as session:
        session.get(Field, "F-01").boundary_revision += 1
    with factory() as session:
        assert seasonal_water(session, OWNER, "F-01", 20).rows == []


def test_known_non_arable_land_rejected_without_geographic_shortcut(factory, monkeypatch):
    from field_shift.modules.rotations.handoff import service

    monkeypatch.setattr(service, "land_status", lambda *args: "ice")
    with factory() as session, pytest.raises(HTTPException) as error:
        submit(session, OWNER, "F-01", body())
    assert error.value.status_code == 422


def test_list_resumes_only_owned_current_boundary_jobs(factory):
    from field_shift.modules.rotations.handoff.service import list_saved

    first = queued(factory)
    with factory() as session:
        assert [j.job_id for j in list_saved(session, OWNER, "F-01")] == [first.job_id]
    with factory.begin() as session:
        session.get(Field, "F-01").boundary_revision += 1
    with factory() as session:
        assert list_saved(session, OWNER, "F-01") == []


def test_execute_checkpoints_then_retries_without_reacquisition(factory, monkeypatch):
    from field_shift.jobs.worker import ModelDataUnavailable
    from field_shift.modules.rotations.handoff.worker import execute

    first = queued(factory)
    with factory.begin() as session:
        lease = repository.claim(session, first.job_id, lease_seconds=135)
    phases = []

    class Client:
        fail = True

        def call(self, phase, payload):
            phases.append(phase)
            if phase == "acquire":
                return payload | {
                    "engine_version": "unit",
                    "source_commit": "unit",
                    "input_sha256": "a" * 64,
                    "evidence_sha256": "b" * 64,
                    "acquired_at": datetime.now(UTC).isoformat(),
                }
            if self.fail:
                raise HandoffUnavailable("unit injected outage after checkpoint")
            return {
                key: payload[key]
                for key in (
                    "operation",
                    "engine_version",
                    "source_commit",
                    "input_sha256",
                    "evidence_sha256",
                    "acquired_at",
                )
            } | {"status": "available", "data_kind": "hybrid_scenario", "limitations": []}

    client = Client()
    correlations = []

    def create_client(*, correlation_id):
        correlations.append(correlation_id)
        return client

    # The durable job UUID crosses the private boundary; the attempt token never does.
    monkeypatch.setattr("field_shift.modules.rotations.handoff.worker.HandoffClient", create_client)
    with pytest.raises(ModelDataUnavailable):
        execute(factory, lease)
    client.fail = False
    result = execute(factory, lease)
    assert phases == ["acquire", "evaluate", "evaluate"]
    assert correlations == [lease.job_id, lease.job_id]
    assert lease.token not in correlations
    with factory.begin() as session:
        assert succeed(session, lease, result)
        assert (
            session.get(Job, first.job_id).result["evidence"]["parameters"]
            == lease.inputs["parameters"]
        )


def test_cached_evidence_cannot_change_bound_field_or_land(factory):
    from field_shift.modules.rotations.handoff.worker import execute

    first = queued(factory)
    with factory.begin() as session:
        lease = repository.claim(session, first.job_id, lease_seconds=135)
    checkpoint(factory, lease, {"operation": "forecast", "parameters": {}, "land_status": "arable"})

    class MustNotRun:
        def call(self, *args):
            raise AssertionError("mismatched checkpoint must not execute")

    with pytest.raises(InvalidModelInput, match="bound field"):
        execute(factory, lease, MustNotRun())


def test_legacy_job_inputs_cannot_bypass_handoff_scope():
    from pydantic import ValidationError

    from field_shift.jobs.schemas import JobInputs

    with pytest.raises(ValidationError):
        JobInputs.model_validate({"analysis": "rotation_handoff", "season": 2027})


@pytest.mark.parametrize("change", ["farm", "geometry"])
def test_geometry_bytes_and_farm_are_rechecked_even_without_revision_change(factory, change):
    first = queued(factory)
    with factory.begin() as session:
        lease = repository.claim(session, first.job_id, lease_seconds=135)
    with factory.begin() as session:
        field = session.get(Field, "F-01")
        if change == "farm":
            field.farm_id = 2
        else:
            field.geometry = {
                "type": "Polygon",
                "coordinates": [[[31, 50], [31.01, 50], [31.01, 50.01], [31, 50.01], [31, 50]]],
            }
    with factory.begin() as session:
        assert succeed(session, lease, {"stale": True})
    with factory() as session:
        assert session.get(Job, first.job_id).status == JobStatus.FAILED
