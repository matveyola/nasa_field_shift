from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from field_shift.api import create_app
from field_shift.config import Settings
from field_shift.db import build_session_factory
from field_shift.models import metadata
from field_shift.modules.farm.models import Farm
from field_shift.modules.fields.models import Field
from field_shift.modules.rotations.models import FarmRotationDraft, RotationPreference
from field_shift.seed import seed_demo


@pytest.fixture
def database():
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
def client(database) -> Iterator[TestClient]:
    with TestClient(create_app(Settings(), session_factory=database)) as client:
        yield client


def payload(**overrides):
    return {
        "client_id": str(uuid4()),
        "name": "2027 rotation",
        "start_season": 2027,
        "field_ids": ["F-01", "F-02", "F-03"],
        "crops": [{"crop": "Wheat"}, {"crop": "Soy"}, {"crop": "Maize"}],
        **overrides,
    }


def test_generate_persist_restore_confirm_and_replay_do_not_change_actual_crop(client, database):
    body = payload()
    baseline = client.get("/api/fields").json()
    response = client.post("/api/rotations/planning/drafts", json=body)
    assert response.status_code == 201, response.text
    plan = response.json()
    assert plan["result"]["status"] == "optimal"
    assert len(plan["result"]["assignments"]) == 9
    assert plan["applied_at"] is None
    assert client.post("/api/rotations/planning/drafts", json=body).json()["id"] == plan["id"]
    assert client.get("/api/rotations/planning/drafts").json()[0]["id"] == plan["id"]
    restored = client.get("/api/rotations/planning/drafts/" + plan["id"]).json()
    assert restored["result"] == plan["result"]
    # Generating a draft does not mutate an existing crop sequence.
    with database() as session:
        assert len(list(session.scalars(select(RotationPreference)))) == 0
    endpoint = "/api/rotations/planning/drafts/" + plan["id"] + "/apply"
    applied = client.post(endpoint)
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied_at"] is not None
    assert client.post(endpoint).json()["applied_at"] == applied.json()["applied_at"]
    assert client.get("/api/fields").json() == baseline
    with database() as session:
        for field_id in body["field_ids"]:
            preference = session.get(RotationPreference, (field_id, 2026))
            assert preference.main_plan_id == "plan-a"
            assert preference.plan_a_crops == [
                row["crop"] for row in plan["result"]["assignments"] if row["field_id"] == field_id
            ]


@pytest.mark.parametrize("change", ["boundary", "manual_plan", "crop", "area"])
def test_stale_field_or_plan_causes_atomic_rejection(client, database, change):
    plan = client.post("/api/rotations/planning/drafts", json=payload()).json()
    with database() as session:
        field = session.get(Field, "F-03")
        if change == "boundary":
            field.boundary_revision += 1
        elif change == "crop":
            field.crop = "Peas"
        elif change == "area":
            field.area_ha += 0.1
        else:
            session.add(RotationPreference(field_id="F-03", season=2026, main_plan_id="plan-b"))
        session.commit()
    response = client.post("/api/rotations/planning/drafts/" + plan["id"] + "/apply")
    assert response.status_code == 409
    with database() as session:
        assert session.get(FarmRotationDraft, plan["id"]).applied_at is None
        # Earlier fields in the same request have not been partially changed.
        assert session.get(RotationPreference, ("F-01", 2026)) is None
        assert session.get(RotationPreference, ("F-02", 2026)) is None


def test_infeasible_plan_is_saved_with_diagnostics_but_cannot_be_applied(client):
    body = payload(
        crops=[{"crop": "Wheat", "max_share_percent": 20}, {"crop": "Soy", "max_share_percent": 20}]
    )
    plan = client.post("/api/rotations/planning/drafts", json=body).json()
    assert plan["result"]["status"] == "infeasible"
    assert plan["result"]["diagnostics"]
    assert client.post("/api/rotations/planning/drafts/" + plan["id"] + "/apply").status_code == 409


def test_retry_key_cannot_be_reused_for_different_constraints(client):
    body = payload()
    assert client.post("/api/rotations/planning/drafts", json=body).status_code == 201
    assert (
        client.post("/api/rotations/planning/drafts", json={**body, "name": "Other"}).status_code
        == 409
    )


def test_context_and_drafts_are_farm_scoped_and_unknown_fields_rejected(client, database):
    with database() as session:
        session.add(
            Farm(
                id=2,
                name="Other",
                season=2026,
                next_field_number=1,
                irrigation_available=False,
                priority="Soil",
            )
        )
        session.flush()
        original = session.get(Field, "F-01")
        session.add(
            Field(
                id="OTHER",
                farm_id=2,
                name="Other field",
                crop="Soy",
                season=2026,
                area_ha=1,
                geometry=original.geometry,
            )
        )
        session.commit()
    context = client.get("/api/rotations/planning/context?start_season=2027").json()
    assert len(context["fields"]) == 3
    assert all(field["id"] != "OTHER" for field in context["fields"])
    assert (
        client.post("/api/rotations/planning/drafts", json=payload(field_ids=["OTHER"])).status_code
        == 404
    )
    assert client.get("/api/rotations/planning/drafts/" + str(uuid4())).status_code == 404
    assert client.get("/api/rotations/planning/context?start_season=2100").status_code == 422


def test_current_geometry_is_not_used_to_plan_past_seasons(client):
    assert client.get("/api/rotations/planning/context?start_season=2025").status_code == 409
    assert (
        client.post("/api/rotations/planning/drafts", json=payload(start_season=2025)).status_code
        == 409
    )
