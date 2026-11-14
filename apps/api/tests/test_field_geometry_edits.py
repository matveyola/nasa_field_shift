from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import insert, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from field_shift.api import create_app
from field_shift.db import build_engine, build_session_factory
from field_shift.models import metadata
from field_shift.modules.farm.models import Farm
from field_shift.modules.fields.models import Field
from field_shift.seed import seed_demo


def _polygon(ring: list[list[float]]) -> dict:
    return {"type": "Polygon", "coordinates": [ring]}


def _rectangle(west: float, south: float, east: float, north: float) -> dict:
    return _polygon([[west, south], [east, south], [east, north], [west, north], [west, south]])


@pytest.fixture
def database() -> Iterator[sessionmaker[Session]]:
    factory = build_session_factory("sqlite://")
    engine = factory.kw["bind"]
    metadata.create_all(engine)
    with factory() as session:
        seed_demo(session)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def client(database: sessionmaker[Session]) -> Iterator[TestClient]:
    with TestClient(create_app(session_factory=database)) as client:
        yield client


def _add_field(client: TestClient, geometry: dict) -> str:
    response = client.post(
        "/api/fields",
        json={"name": "Boundary test", "crop": "Soy", "area_ha": 120, "geometry": geometry},
    )
    assert response.status_code == 201
    assert response.json()["boundary_revision"] == 0
    assert response.json()["planting"] is None
    return f"/api/fields/{response.json()['id']}"


def _update(client: TestClient, path: str, geometry: dict | None, revision: int = 0):
    if "/planting/" in path:
        return client.put(path, json={"geometry": geometry, "boundary_revision": revision})
    return client.patch(path, json={"geometry": geometry, "boundary_revision": revision})


def _last_field(client: TestClient) -> dict:
    response = client.get("/api/fields")
    assert response.status_code == 200
    return response.json()[-1]


def test_boundary_and_planting_save_independent_areas(client: TestClient) -> None:
    original = _rectangle(0, 0, 0.01, 0.01)
    field = _add_field(client, original)
    ring = original["coordinates"][0]
    reordered = _polygon([ring[2], ring[1], ring[0], ring[3], ring[2]])
    unchanged = _update(client, f"{field}/boundary", reordered)
    assert unchanged.status_code == 200
    assert unchanged.json()["boundary_revision"] == 0
    assert unchanged.json()["area_ha"] == 120

    planting = _rectangle(0, 0, 0.005, 0.01)
    planted = _update(client, f"{field}/planting/2026", planting)
    assert planted.status_code == 200
    assert planted.json()["geometry"] == original
    assert planted.json()["planting"]["area_ha"] == pytest.approx(61.82, abs=0.02)
    assert planted.json()["planting"]["season"] == 2026
    assert planted.json()["boundary_revision"] == 0

    expanded = _rectangle(0, 0, 0.02, 0.01)
    edited = _update(client, f"{field}/boundary", expanded)
    assert edited.status_code == 200
    assert edited.json()["boundary_revision"] == 1
    assert edited.json()["area_ha"] == pytest.approx(247.29, abs=0.02)
    assert edited.json()["planting"] == planted.json()["planting"]
    assert edited.json()["name"] == "Boundary test"
    assert edited.json()["crop"] == "Soy"
    assert _last_field(client) == edited.json()
    assert client.get("/api/farm").json()["total_area_ha"] == pytest.approx(302.89, abs=0.02)

    for path in (f"{field}/boundary", f"{field}/planting/2026"):
        stale = _update(client, path, original)
        assert stale.status_code == 409
        assert "boundary changed" in stale.json()["detail"]
    shrink = _update(client, f"{field}/boundary", _rectangle(0, 0, 0.004, 0.01), 1)
    assert shrink.status_code == 409
    assert "exclude part of the planted area" in shrink.json()["detail"]
    assert _last_field(client) == edited.json()

    full = _update(client, f"{field}/planting/2026", None, 1)
    assert full.status_code == 200
    assert full.json()["planting"] is None
    assert _last_field(client) == full.json()


@pytest.mark.parametrize("crossing_height", [0.02, 0.03])
def test_concave_field_rejects_edges_outside_with_vertices_inside(
    client: TestClient, crossing_height: float
) -> None:
    field = _add_field(
        client,
        _polygon(
            [
                [0, 0],
                [0.03, 0],
                [0.03, 0.03],
                [0.02, 0.03],
                [0.02, 0.01],
                [0.01, 0.01],
                [0.01, 0.03],
                [0, 0.03],
                [0, 0],
            ]
        ),
    )
    crossing = _polygon(
        [
            [0.005, crossing_height],
            [0.025, crossing_height],
            [0.015, 0.005],
            [0.005, crossing_height],
        ]
    )
    response = _update(client, f"{field}/planting/2026", crossing)
    assert response.status_code == 409
    assert response.json()["detail"] == "The planted area must be within the field boundary."
    assert _last_field(client)["planting"] is None


def test_read_and_write_planting_in_active_farm_season(
    client: TestClient, database: sessionmaker[Session]
) -> None:
    field = _add_field(client, _rectangle(0, 0, 0.01, 0.01))
    first = _rectangle(0, 0, 0.005, 0.01)
    assert _update(client, f"{field}/planting/2025", first).status_code == 409
    assert _update(client, f"{field}/planting/2026", first).status_code == 200
    with database.begin() as session:
        session.get(Farm, 1).season = 2027
    assert _last_field(client)["planting"] is None
    second = _rectangle(0.005, 0, 0.01, 0.01)
    assert _update(client, f"{field}/planting/2027", second).status_code == 200
    assert _last_field(client)["planting"]["geometry"] == second
    with database.begin() as session:
        session.get(Farm, 1).season = 2026
    assert _last_field(client)["planting"]["geometry"] == first


def test_invalid_geometry_and_revision_cannot_change_boundary(client: TestClient) -> None:
    field = _add_field(client, _rectangle(0, 0, 0.01, 0.01))
    before = _last_field(client)
    invalid = _polygon([[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]])
    for geometry in (invalid, _rectangle(0, 0, 1, 1)):
        assert _update(client, f"{field}/boundary", geometry).status_code == 422
    assert _update(client, f"{field}/boundary", before["geometry"], -1).status_code == 422
    assert _last_field(client) == before


def test_migration_keeps_existing_boundary_and_initializes_revision(tmp_path: Path) -> None:
    api = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'boundary-edits.sqlite3').as_posix()}"
    config = Config(str(api / "alembic.ini"))
    config.set_main_option("script_location", str(api / "migrations"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0003_naip_demo_fields")
    engine = build_engine(url)
    geometry = _rectangle(0, 0, 0.01, 0.01)
    with engine.begin() as connection:
        connection.execute(
            insert(Farm.__table__).values(
                id=1,
                name="Test",
                season=2026,
                next_field_number=2,
                irrigation_available=False,
                priority="Planning",
            )
        )
        connection.execute(
            insert(Field.__table__).values(
                id="F-01",
                farm_id=1,
                name="Saved field",
                crop="Soy",
                area_ha=120,
                season=2026,
                geometry=geometry,
            )
        )
    command.upgrade(config, "0004_field_plantings")
    with engine.connect() as connection:
        revision, boundary, area = connection.execute(
            select(Field.boundary_revision, Field.geometry, Field.area_ha)
        ).one()
        assert (revision, boundary, area) == (0, geometry, 120)
        assert inspect(connection).has_table("field_plantings")
    command.downgrade(config, "0003_naip_demo_fields")
    with engine.connect() as connection:
        assert not inspect(connection).has_table("field_plantings")
        assert connection.execute(select(Field.geometry)).scalar_one() == geometry
    engine.dispose()
