"""Isolated authenticated router handoff, using explicit test dependency overrides."""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from field_shift.modules.access.dependencies import require_principal
from field_shift.modules.rotations.scenario_planning.routes import create_router
from field_shift.modules.rotations.scenario_planning.service import RunService

from .test_persistence import EDITOR, VIEWER, SyntheticResolver


def test_isolated_router_permissions_scope_and_listing(database, farm_data):
    engine, _, _ = database
    app = FastAPI()
    app.state.session_factory = sessionmaker(engine, expire_on_commit=False)
    app.include_router(create_router(RunService(SyntheticResolver())), prefix="/api")
    app.dependency_overrides[require_principal] = lambda: EDITOR
    with TestClient(app) as client:
        response = client.post("/api/planning-v1/farms/1/runs", json=farm_data[0])
        assert response.status_code == 202
        run = response.json()
        assert run["status"] == "queued"
        assert client.post("/api/planning-v1/farms/1/runs", json=farm_data[0]).json() == run
        assert client.get("/api/planning-v1/farms/1/runs").json() == [run]
        assert client.get(f"/api/planning-v1/farms/1/runs/{run['run_id']}").json() == run
        mismatch = client.post("/api/planning-v1/farms/2/runs", json=farm_data[0])
        assert mismatch.status_code == 422 and mismatch.json()["code"] == "invalid_input"
        assert client.get(f"/api/planning-v1/farms/2/runs/{run['run_id']}").status_code == 404
        app.dependency_overrides[require_principal] = lambda: VIEWER
        assert client.get("/api/planning-v1/farms/1/runs").status_code == 200
        forbidden = client.post("/api/planning-v1/farms/1/runs", json=farm_data[0])
        assert forbidden.status_code == 403 and forbidden.json()["code"] == "forbidden"
        assert "planning-service" in json.dumps(forbidden.json())


def test_http_strict_dates_and_errors(database, farm_data):
    engine, _, _ = database
    app = FastAPI()
    app.state.session_factory = sessionmaker(engine, expire_on_commit=False)
    app.include_router(create_router(RunService(SyntheticResolver())), prefix="/api")
    app.dependency_overrides[require_principal] = lambda: EDITOR
    with TestClient(app) as client:
        # Strings in numeric fields remain invalid; JSON date strings are valid.
        farm_data[0]["fields"][0]["area_ha"] = "10"
        result = client.post("/api/planning-v1/farms/1/runs", json=farm_data[0])
        assert result.status_code == 422
        assert result.json()["code"] == "invalid_input"
        assert result.json()["reasons"]
        assert client.get("/api/planning-v1/farms/1/runs?limit=1000").status_code == 422


def test_oversized_body_rejected_before_json_parsing(database):
    engine, _, _ = database
    app = FastAPI()
    app.state.session_factory = sessionmaker(engine, expire_on_commit=False)
    app.include_router(create_router(RunService(SyntheticResolver())), prefix="/api")
    app.dependency_overrides[require_principal] = lambda: EDITOR
    with TestClient(app) as client:
        response = client.post(
            "/api/planning-v1/farms/1/runs",
            content=b"x" * 8_000_001,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422 and response.json()["code"] == "invalid_input"
