import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from field_shift.contracts.planning_v1 import ProfileWriteRequest, WeatherScenarioSet
from field_shift.db import build_session_factory
from field_shift.models import metadata
from field_shift.modules.access.service import Principal
from field_shift.modules.fields.geometry import area_hectares
from field_shift.modules.fields.models import Field
from field_shift.modules.fields.planning_profile.models import ProfileSnapshot
from field_shift.modules.fields.planning_profile.routes import router
from field_shift.modules.fields.planning_profile.service import ProfileError, ProfileRepository
from field_shift.modules.fields.planning_profile.weather_archive import WeatherIssue, preserve_issue
from field_shift.seed import seed_demo


@pytest.fixture
def workspace(tmp_path, profile):
    factory = build_session_factory(f"sqlite:///{tmp_path / 'profiles.db'}")
    metadata.create_all(factory.kw["bind"])
    with factory() as session:
        seed_demo(session)
        field = session.get(Field, "F-01")
        document = profile.model_dump(mode="json")
        document.update(
            farm_id=str(field.farm_id),
            field_id=field.id,
            geometry_revision=field.boundary_revision,
            geometry={**field.geometry, "crs": "EPSG:4326"},
            area_ha=field.area_ha,
        )
        request = ProfileWriteRequest.model_validate_json(
            json.dumps(
                {
                    "request_id": "write-1",
                    "expected_revision": None,
                    "profile": document,
                }
            )
        )
    yield factory, request
    factory.kw["bind"].dispose()


def test_replay_cas_and_immutable_history(workspace):
    factory, request = workspace
    with factory() as session:
        repo = ProfileRepository(session)
        first = repo.write("1", "F-01", request)
        assert first.profile_revision == 1
        assert repo.write("1", "F-01", request) == first
        assert len(session.scalars(select(ProfileSnapshot)).all()) == 1
        changed = request.model_copy(update={"request_id": "write-2"})
        with pytest.raises(ProfileError, match="revision"):
            repo.write("1", "F-01", changed)
        changed = changed.model_copy(update={"expected_revision": 1})
        second = repo.write("1", "F-01", changed)
        assert second.profile_revision == 2
        assert second.snapshot_id != first.snapshot_id
        assert repo.get_snapshot("1", "F-01", first.snapshot_id) == first
        assert second.history == first.history
        with pytest.raises(ProfileError, match="different content"):
            repo.write("1", "F-01", request.model_copy(update={"expected_revision": 2}))
        with pytest.raises(ProfileError, match="not found"):
            repo.get_snapshot("2", "F-01", first.snapshot_id)


def test_boundary_invalidates_latest_but_keeps_reproducible_snapshot(workspace):
    factory, request = workspace
    with factory() as session:
        repo = ProfileRepository(session)
        old = repo.write("1", "F-01", request)
        session.get(Field, "F-01").boundary_revision += 1
        session.commit()
        with pytest.raises(ProfileError, match="Boundary changed"):
            repo.latest("1", "F-01")
        assert repo.get_snapshot("1", "F-01", old.snapshot_id) == old
        with pytest.raises(ProfileError, match="boundary"):
            repo.write(
                "1",
                "F-01",
                request.model_copy(
                    update={
                        "request_id": "boundary",
                        "expected_revision": 1,
                    }
                ),
            )


def test_topology_and_area(workspace):
    factory, request = workspace
    with factory() as session:
        profile = request.profile.model_copy(update={"area_ha": 99999.0})
        with pytest.raises(ProfileError, match="Area differs"):
            ProfileRepository(session).write(
                "1",
                "F-01",
                request.model_copy(
                    update={
                        "profile": profile,
                    }
                ),
            )
    assert area_hectares(request.profile.geometry.coordinates[0]) > 0


@pytest.mark.parametrize("role,expected", [("viewer", 403), ("editor", 200), ("admin", 200)])
def test_router_roles_and_scope(workspace, role, expected):
    factory, payload = workspace
    app = FastAPI()
    app.state.session_factory = factory
    actor = Principal(1, 1, "Synthetic Test", None, role)

    @app.middleware("http")
    async def test_auth(request: Request, call_next):
        request.state.principal = actor
        return await call_next(request)

    app.include_router(router, prefix="/api")
    with TestClient(app) as client:
        path = "/api/planning-v1/farms/1/fields/F-01/profile"
        response = client.put(path, json=payload.model_dump(mode="json"))
        assert response.status_code == expected, response.text
        assert client.get(path.replace("farms/1", "farms/2")).status_code == 404
        assert client.put(
            path.replace("F-01", "F-02"), json=payload.model_dump(mode="json")
        ).status_code in {403, 422}
        if expected == 200:
            assert client.get(path).json() == response.json()


def test_weather_archive_is_farm_scoped_and_immutable(workspace):
    factory, _ = workspace
    root = Path(__file__).resolve().parents[4]
    doc = json.loads((root / "contracts/planning-v1/examples/planning-contracts.json").read_text())
    weather = WeatherScenarioSet.model_validate_json(json.dumps(doc["weather"]))
    with factory() as session:
        assert preserve_issue(session, 1, weather) == weather
        assert preserve_issue(session, 1, weather) == weather
        session.commit()
        assert len(session.scalars(select(WeatherIssue)).all()) == 1
        with pytest.raises(ProfileError, match="changed data"):
            preserve_issue(session, 1, weather.model_copy(update={"model_version": "changed"}))


def test_migration_roundtrip_preserves_baseline(tmp_path):
    verify_migration(f"sqlite:///{tmp_path / 'migration.db'}")


def verify_migration(url):
    api = Path(__file__).resolve().parents[2]
    config = Config(str(api / "alembic.ini"))
    config.set_main_option("script_location", str(api / "migrations"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0010_precision_plans")
    factory = build_session_factory(url)
    try:
        with factory() as session:
            seed_demo(session)
            before = [(f.id, f.name, f.geometry) for f in session.scalars(select(Field))]
        command.upgrade(config, "0011_field_profiles")
        assert "planning_field_profiles" in inspect(factory.kw["bind"]).get_table_names()
        with factory() as session:
            assert [(f.id, f.name, f.geometry) for f in session.scalars(select(Field))] == before
        command.downgrade(config, "0010_precision_plans")
        assert "planning_field_profiles" not in inspect(factory.kw["bind"]).get_table_names()
        with factory() as session:
            assert [(f.id, f.name, f.geometry) for f in session.scalars(select(Field))] == before
        command.upgrade(config, "0011_field_profiles")
    finally:
        factory.kw["bind"].dispose()


def test_malformed_json_and_openapi_use_frozen_contract(workspace):
    factory, payload = workspace
    app = FastAPI()
    app.state.session_factory = factory
    app.include_router(router, prefix="/api")
    path = "/api/planning-v1/farms/1/fields/F-01/profile"
    with TestClient(app) as client:
        result = client.put(path, content="{broken", headers={"Content-Type": "application/json"})
        assert result.status_code == 422
        assert result.json()["code"] == "invalid_input"
        assert "ProfileWriteRequest" in app.openapi()["components"]["schemas"]


def test_real_session_authentication_and_csrf(workspace):
    from field_shift.api import create_app
    from field_shift.config import Settings

    factory, payload = workspace
    token = "synthetic-bootstrap-only-32-characters"
    app = create_app(
        Settings(access_mode="team", access_bootstrap_token=token), session_factory=factory
    )
    app.include_router(router, prefix="/api")
    path = "/api/planning-v1/farms/1/fields/F-01/profile"
    with TestClient(app, headers={"X-Field-Shift-Request": "1"}) as client:
        assert client.get(path).status_code == 401
        response = client.post(
            "/api/auth/setup",
            json={
                "email": "profile-test@example.org",
                "name": "Synthetic Test",
                "password": "synthetic-test-password-32-characters",
                "token": token,
            },
        )
        assert response.status_code == 200, response.text
        assert client.put(path, json=payload.model_dump(mode="json")).status_code == 403
        state = client.get("/api/auth/session").json()
        client.headers["X-CSRF-Token"] = state["csrf_token"]
        response = client.put(path, json=payload.model_dump(mode="json"))
        assert response.status_code == 200, response.text
        assert client.get(path).status_code == 200


def test_holes_topology_and_axis_order_are_not_silently_accepted(workspace):
    from field_shift.contracts.planning_v1.field import FieldGeometry
    from field_shift.modules.fields.planning_profile.service import validate_geometry

    factory, request = workspace
    # Outside hole: individually closed valid rings, invalid full polygon topology.
    geometry = request.profile.geometry.model_dump(mode="json")
    geometry["coordinates"].append([[0, 0], [0.01, 0], [0.01, 0.01], [0, 0]])
    parsed = FieldGeometry.model_validate_json(json.dumps(geometry))
    with pytest.raises(ProfileError, match="topology"):
        validate_geometry(request.profile.model_copy(update={"geometry": parsed}))
    # Swapped coordinates can be numerically legal in some regions; current-boundary
    # equality is the extra guard, rather than guessing axis order from continent.
    geometry = request.profile.geometry.model_dump(mode="json")
    geometry["coordinates"] = [
        [[lat, lon] for lon, lat in ring] for ring in geometry["coordinates"]
    ]
    # F-01 longitude is outside latitude bounds, so the strict contract rejects it.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FieldGeometry.model_validate_json(json.dumps(geometry))
