"""Exercise the registered HTTP boundary with no private service or provider calls."""

import pytest
from fastapi.testclient import TestClient

from field_shift.api import create_app
from field_shift.db import build_session_factory
from field_shift.models import metadata
from field_shift.seed import seed_demo


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("FIELD_SHIFT_ROTATION_MODEL_URL", raising=False)
    monkeypatch.delenv("FIELD_SHIFT_ROTATION_MODEL_TOKEN", raising=False)
    factory = build_session_factory("sqlite://")
    metadata.create_all(factory.kw["bind"])
    with factory() as session:
        seed_demo(session)
    with TestClient(create_app(session_factory=factory)) as browser:
        yield browser
    factory.kw["bind"].dispose()


def test_registered_catalog_reports_missing_runtime_without_fake_execution(client):
    response = client.get("/api/rotations/handoff/catalog")
    assert response.status_code == 200
    catalog = response.json()
    assert catalog["status"] == "unavailable"
    assert catalog["empirical_status"] == "not_validated"
    assert catalog["reasons"]
    assert client.get("/api/rotations/handoff/fields/F-03/jobs").json() == []


def test_registered_evidence_routes_scope_empty_results_and_missing_fields(client):
    endpoint = "/api/rotations/handoff/fields/F-03/seasonal-water"
    response = client.get(endpoint)
    assert response.status_code == 200
    assert response.json()["field_id"] == "F-03"
    assert response.json()["rows"] == []
    assert client.get(endpoint + "?limit=21").status_code == 422
    assert client.get("/api/rotations/handoff/fields/absent/jobs").status_code == 404


def test_strategy_context_and_scoped_operations_use_registered_routes(client):
    endpoint = "/api/rotations/handoff/fields/F-03"
    response = client.get(endpoint + "/strategy-context")
    assert response.status_code == 200
    context = response.json()
    assert context["field_id"] == "F-03"
    assert context["area_ha"] > 0
    assert context["min_year"] == 2022 and context["max_steps"] == 6
    assert client.get(endpoint + "/strategy-scenarios").json() == []
    request = {
        "request_id": "strategy-http",
        "geometry_revision": context["geometry_revision"],
        "mode": "sequence",
        "planting_date": "2040-04-15",
        "step_count": 1,
        "priority": "water_deficit",
    }
    headers = {"X-Field-Shift-Request": "1"}
    pending = client.post(endpoint + "/strategy-jobs", json=request, headers=headers)
    assert pending.status_code == 202
    job = pending.json()
    assert client.get(endpoint + "/jobs/" + job["job_id"]).json() == job
    save = {
        "geometry_revision": context["geometry_revision"],
        "choices": [{"job_id": job["job_id"], "crop_id": "barley"}],
    }
    assert (
        client.post(endpoint + "/strategy-scenarios", json=save, headers=headers).status_code == 409
    )
    assert client.get(endpoint + "/strategy-scenarios").json() == []


def test_registered_submission_rejects_oversized_body_before_creating_a_job(client):
    response = client.post(
        "/api/rotations/handoff/fields/F-03/jobs",
        content=b'{"parameters":{"oversized":"' + b"x" * 70_001 + b'"}}',
        headers={"Content-Type": "application/json", "X-Field-Shift-Request": "1"},
    )
    assert response.status_code == 413
    assert client.get("/api/rotations/handoff/fields/F-03/jobs").json() == []


@pytest.mark.parametrize("action", ["strategy-jobs", "strategy-scenarios"])
def test_strategy_mutations_reject_oversized_request_bodies(client, action):
    response = client.post(
        "/api/rotations/handoff/fields/F-03/" + action,
        content=b'{"oversized":"' + b"x" * 70_001 + b'"}',
        headers={"Content-Type": "application/json", "X-Field-Shift-Request": "1"},
    )
    assert response.status_code == 413
