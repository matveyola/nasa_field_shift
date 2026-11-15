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


@pytest.mark.parametrize("season_count", [1, 2, 6])
def test_variable_duration_survives_apply_read_and_manual_save(client, database, season_count):
    body = payload(
        season_count=season_count,
        require_two_crops=season_count > 1,
        crops=[{"crop": "Soy", "min_break_years": 0}, {"crop": "Maize"}],
    )
    baseline = client.get("/api/fields").json()
    response = client.post("/api/rotations/planning/drafts", json=body)
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["season_count"] == draft["request"]["season_count"] == season_count
    assert draft["result"]["status"] in {"optimal", "feasible"}
    assert client.get("/api/rotations/planning/drafts").json()[0]["season_count"] == season_count
    url = "/api/rotations/planning/drafts/" + draft["id"]
    assert client.get(url).json()["season_count"] == season_count
    applied = client.post(url + "/apply")
    assert applied.status_code == 200, applied.text
    assert client.post(url + "/apply").json() == applied.json()
    assert client.get("/api/fields").json() == baseline
    with database() as session:
        for field_id in body["field_ids"]:
            expected = [
                row["crop"] for row in draft["result"]["assignments"] if row["field_id"] == field_id
            ]
            assert len(expected) == season_count
            assert session.get(RotationPreference, (field_id, 2026)).plan_a_crops == expected
    # The manual editor's transport must accept the whole applied sequence.
    manual = {"field_id": "F-01", "season": 2026, "crops": ["Peas"] * season_count}
    saved = client.put("/api/rotations/plans/plan-a", json=manual)
    assert saved.status_code == 200 and saved.json()["crops"] == manual["crops"]
    schedules = client.get("/api/rotations?field_id=F-01&season=2026").json()["plans"]
    assert [row["crop"] for row in schedules[0]["crops"]] == manual["crops"]
    assert [row["year"] for row in schedules[0]["crops"]] == list(range(2027, 2027 + season_count))


def test_legacy_draft_without_duration_still_replays_as_three_seasons(client, database):
    body = payload()
    draft = client.post("/api/rotations/planning/drafts", json=body).json()
    with database() as session:
        stored = session.get(FarmRotationDraft, draft["id"])
        stored.request = {k: v for k, v in stored.request.items() if k != "season_count"}
        stored.result = {
            k: v
            for k, v in stored.result.items()
            if k
            not in {
                "generation_nodes",
                "generation_limit",
                "generation_complete",
                "time_limit_seconds",
            }
        } | {"method": "bounded_branch_and_bound_v1"}
        session.commit()
    replay = client.post("/api/rotations/planning/drafts", json=body)
    assert replay.status_code == 201 and replay.json()["id"] == draft["id"]
    assert replay.json()["season_count"] == replay.json()["request"]["season_count"] == 3
    assert replay.json()["result"]["method"] == "bounded_branch_and_bound_v1"
    assert client.get("/api/rotations/planning/drafts").json()[0]["season_count"] == 3
    changed = client.post("/api/rotations/planning/drafts", json={**body, "season_count": 2})
    assert changed.status_code == 409
    assert (
        client.post("/api/rotations/planning/drafts/" + draft["id"] + "/apply").status_code == 200
    )


def test_context_reports_requested_duration_bounds_and_broad_catalog(client):
    context = client.get("/api/rotations/planning/context?start_season=2095&season_count=6")
    assert context.status_code == 200
    data = context.json()
    assert data["season_count"] == data["max_season_count"] == 6
    assert data["min_season_count"] == 1
    assert {c["crop"] for c in data["crops"]} == {
        "Wheat",
        "Maize",
        "Soy",
        "Barley",
        "Sunflower",
        "Rapeseed",
        "Peas",
    }
    assert (
        client.get("/api/rotations/planning/context?start_season=2100&season_count=1").status_code
        == 200
    )
    for query in ("start_season=2096&season_count=6", "season_count=7", "season_count=0"):
        assert client.get("/api/rotations/planning/context?" + query).status_code == 422


@pytest.mark.parametrize("defect", ["duplicate", "missing", "outside", "foreign"])
def test_apply_rejects_incomplete_or_duplicate_variable_years_atomically(client, database, defect):
    draft = client.post("/api/rotations/planning/drafts", json=payload(season_count=2)).json()
    with database() as session:
        stored = session.get(FarmRotationDraft, draft["id"])
        rows = [dict(row) for row in stored.result["assignments"]]
        if defect == "duplicate":
            rows[1] = dict(rows[0])
        elif defect == "missing":
            rows.pop()
        elif defect == "outside":
            rows[-1]["season"] = 2029
        else:
            rows[-1]["field_id"] = "FOREIGN"
        stored.result = {**stored.result, "assignments": rows}
        session.commit()
    response = client.post("/api/rotations/planning/drafts/" + draft["id"] + "/apply")
    assert response.status_code == 409
    with database() as session:
        assert not list(session.scalars(select(RotationPreference)))
        assert session.get(FarmRotationDraft, draft["id"]).applied_at is None


@pytest.mark.parametrize("crops", [[], ["Soy"] * 7])
def test_manual_plan_retains_bounded_duration(client, crops):
    response = client.put(
        "/api/rotations/plans/plan-a", json={"field_id": "F-01", "season": 2026, "crops": crops}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("season_count,expected", [(1, "feasible"), (2, "search_limit")])
def test_generation_limited_apply_requires_complete_feasible_result(
    client, database, monkeypatch, season_count, expected
):
    from functools import partial

    from field_shift.modules.rotations import planning_service
    from field_shift.modules.rotations.planner import optimize

    monkeypatch.setattr(planning_service, "optimize", partial(optimize, generation_limit=1))
    response = client.post(
        "/api/rotations/planning/drafts",
        json=payload(
            field_ids=["F-01"],
            season_count=season_count,
            require_two_crops=False,
            crops=[{"crop": "Maize"}, {"crop": "Soy"}],
        ),
    )
    assert response.status_code == 201
    draft = response.json()
    assert draft["result"]["status"] == expected
    assert not draft["result"]["generation_complete"]
    assert draft["result"]["warnings"][0]["code"] == "generation_budget_exhausted"
    applied = client.post("/api/rotations/planning/drafts/" + draft["id"] + "/apply")
    assert applied.status_code == (200 if expected == "feasible" else 409)
    with database() as session:
        stored = session.get(RotationPreference, ("F-01", 2026))
        if expected == "feasible":
            assert stored.plan_a_crops == ["Maize"]
        else:
            assert stored is None
