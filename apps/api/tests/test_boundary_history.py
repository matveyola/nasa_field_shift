from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from field_shift_science.fields.schemas import Polygon
from sqlalchemy import select

from field_shift.api import create_app
from field_shift.db import build_session_factory
from field_shift.models import metadata
from field_shift.modules.fields.history import boundary_for_season
from field_shift.modules.fields.models import Field, FieldBoundaryVersion, PreparedContour
from field_shift.seed import seed_demo


def rectangle(west=30, east=30.02):
    return {
        "type": "Polygon",
        "coordinates": [[[west, 48], [east, 48], [east, 48.01], [west, 48.01], [west, 48]]],
    }


@pytest.fixture
def factory():
    factory = build_session_factory("sqlite://")
    metadata.create_all(factory.kw["bind"])
    with factory() as session:
        seed_demo(session)
    yield factory
    factory.kw["bind"].dispose()


def test_seasonal_history_restore_append_conflicts_and_current_projection(factory):
    with TestClient(create_app(session_factory=factory)) as client:
        new = client.post(
            "/api/fields",
            json={"name": "History", "crop": "Wheat", "area_ha": 10, "geometry": rectangle()},
        ).json()
        path = f"/api/fields/{new['id']}"
        past = rectangle(east=30.01)
        response = client.patch(
            path + "/boundary",
            json={"geometry": past, "boundary_revision": 0, "effective_season": 2025},
        )
        assert response.status_code == 200
        assert response.json()["geometry"] == rectangle()
        assert response.json()["boundary_revision"] == 1
        earlier = client.get(path + "/boundary-history/2024").json()
        assert earlier["earliest_boundary_reused"] is True
        assert earlier["geometry"] == past
        assert client.get(path + "/boundary-history/2025").json()["geometry"] == past
        restored = client.patch(path + "/boundary", json={"geometry": past, "boundary_revision": 1})
        assert restored.status_code == 200 and restored.json()["geometry"] == past
        assert restored.json()["boundary_revision"] == 2
        assert (
            client.patch(
                path + "/boundary", json={"geometry": rectangle(), "boundary_revision": 1}
            ).status_code
            == 409
        )
        history = client.get(path + "/boundary-history").json()
        assert [row["revision"] for row in history] == [2, 1, 0]
        assert history[-1]["geometry"] == rectangle()
        assert client.get("/api/fields/unknown/boundary-history").status_code == 404
        assert (
            client.patch(
                path + "/boundary",
                json={"geometry": rectangle(), "boundary_revision": 2, "effective_season": 2027},
            ).status_code
            == 409
        )


def test_history_geometry_is_used_by_vegetation_and_catalog_is_bounded(factory):
    from field_shift.modules.imagery.vegetation import vegetation_footprint

    with factory.begin() as session:
        field = session.get(Field, "F-01")
        session.add(
            FieldBoundaryVersion(
                field_id=field.id,
                revision=0,
                effective_season=2024,
                geometry=rectangle(),
                area_ha=1,
                created_at=datetime.now(UTC),
                source="test",
            )
        )
    with factory() as session:
        field = session.get(Field, "F-01")
        assert vegetation_footprint(session, field, 2024)[0] == Polygon.model_validate(rectangle())
        assert boundary_for_season(session, field, 2023)[2] is True
    with factory.begin() as session:
        session.add(
            PreparedContour(
                id="harvest-test",
                geometry=rectangle(),
                area_ha=1,
                west=30,
                east=30.02,
                south=48,
                north=48.01,
                year=2023,
                source="NASA Harvest test fixture",
                source_url="https://data.harvestportal.org/",
                license="CC BY",
                region="Fixture",
                imported_at=datetime.now(UTC),
            )
        )
    with TestClient(create_app(session_factory=factory)) as client:
        result = client.get(
            "/api/fields/prepared-contours?west=29.99&east=30.03&south=47.99&north=48.02"
        )
        assert result.status_code == 200 and result.json()["contours"][0]["id"] == "harvest-test"
        assert (
            client.get(
                "/api/fields/prepared-contours?west=20&east=40&south=40&north=50"
            ).status_code
            == 422
        )
        assert client.get("/api/fields/prepared-contours/coverage").json()[0]["count"] == 1
    with factory() as session:
        assert session.scalar(select(PreparedContour.id)) == "harvest-test"
