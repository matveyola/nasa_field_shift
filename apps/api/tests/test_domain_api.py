from base64 import b64decode, b64encode
from collections.abc import Iterator
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from field_shift.api import create_app
from field_shift.db import build_session_factory
from field_shift.errors import ConflictError
from field_shift.jobs.worker import process_job
from field_shift.models import metadata
from field_shift.modules.agent.field_context import build_field_context
from field_shift.modules.agent.provider import AgentAnswer, AgentContext, DemoAgentProvider
from field_shift.modules.agent.schemas import AgentRequest
from field_shift.modules.agent.service import chat, conversation_detail
from field_shift.modules.farm.models import Farm, Observation
from field_shift.modules.fields.models import Field
from field_shift.seed import seed_demo


@pytest.fixture
def database() -> Iterator[sessionmaker[Session]]:
    factory = build_session_factory("sqlite://")
    engine = factory.kw["bind"]
    metadata.create_all(engine)
    with factory() as session:
        seed_demo(session)
        seed_demo(session)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def client(database: sessionmaker[Session]) -> Iterator[TestClient]:
    with TestClient(create_app(session_factory=database)) as client:
        yield client


def test_farm_persists_defaults_observations_and_created_fields(client: TestClient) -> None:
    farm = client.get("/api/farm").json()
    assert farm["total_area_ha"] == 55.6
    assert len(farm["observations"]) == 2
    assert client.patch("/api/farm/defaults", json={"priority": None}).status_code == 422

    response = client.patch("/api/farm/defaults", json={"budget": 0, "equipment": "Drip lines"})
    assert response.status_code == 200
    assert response.json()["budget"] == 0
    assert response.json()["equipment"] == "Drip lines"
    assert client.patch("/api/farm/defaults", json={"equipment": None}).status_code == 200
    assert client.get("/api/farm").json()["defaults"]["equipment"] is None

    geometry = {
        "type": "Polygon",
        "coordinates": [[[30.4, 50.2], [30.5, 50.2], [30.5, 50.3], [30.4, 50.2]]],
    }
    response = client.post(
        "/api/fields",
        json={"name": "Test Field", "crop": "Soy", "area_ha": 10, "geometry": geometry},
    )
    assert response.status_code == 201
    assert response.json()["id"] == "F-04"
    assert client.get("/api/farm").json()["total_area_ha"] == 65.6

    note = {
        "field_id": "F-04",
        "kind": "field_note",
        "text": "Walked the field.",
        "date": "2026-08-02",
    }
    response = client.post("/api/farm/observations", json=note)
    assert response.status_code == 201
    assert client.get("/api/farm").json()["observations"][0] == {"id": 3, **note}
    assert (
        client.post("/api/farm/observations", json={**note, "field_id": "unknown"}).status_code
        == 404
    )


PHOTO = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIx"
    "wcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xA"
    "AUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAM"
    "AwEAAhEDEQA/AL+AD//Z"
)


def test_scouting_photo_location_and_network_retry_are_atomic(client: TestClient) -> None:
    report = {
        "field_id": "F-03",
        "kind": "field_note",
        "text": "Check the leaves at the west edge.",
        "date": "2026-08-04",
        "category": "pests",
        "latitude": 0,
        "longitude": 0,
        "photo_jpeg": PHOTO,
        "client_id": "da548698-8185-4b70-afb9-11d76dc288e3",
    }
    response = client.post("/api/farm/observations", json=report)
    assert response.status_code == 201
    saved = response.json()
    assert saved == {
        "id": 3,
        "has_photo": True,
        **{key: value for key, value in report.items() if key not in {"photo_jpeg", "client_id"}},
    }
    assert client.post("/api/farm/observations", json=report).json() == saved
    assert (
        client.post("/api/farm/observations", json={**report, "category": "disease"}).status_code
        == 409
    )
    assert client.get("/api/farm").json()["observations"][0] == saved
    assert len(client.get("/api/farm").json()["observations"]) == 3

    image = client.get(f"/api/farm/observations/{saved['id']}/photo")
    assert image.content == b64decode(PHOTO)
    assert image.headers["Content-Type"] == "image/jpeg"
    assert image.headers["Cache-Control"] == "private, no-store"
    assert image.headers["X-Content-Type-Options"] == "nosniff"
    assert client.get("/api/farm/observations/1/photo").status_code == 404


def test_scouting_rejects_invalid_coordinates_and_attachments(client: TestClient) -> None:
    report = {"field_id": "F-03", "kind": "field_note", "text": "Crop scouting."}
    for changes in (
        {"latitude": 48.42},
        {"latitude": 91, "longitude": 31},
        {"photo_jpeg": "bad-base64"},
        {"photo_jpeg": b64encode(b"<svg></svg>").decode()},
        {"photo_jpeg": "AAAA" * 175_000},
    ):
        assert client.post("/api/farm/observations", json={**report, **changes}).status_code == 422
    assert len(client.get("/api/farm").json()["observations"]) == 2


def test_observation_photo_access_follows_field_ownership(client, database) -> None:
    with database() as session:
        farm = Farm(
            id=2,
            name="Other farm",
            season=2026,
            next_field_number=2,
            irrigation_available=False,
            priority="Planning",
        )
        session.add(farm)
        session.flush()
        source_field = session.get(Field, "F-01")
        session.add(
            Field(
                id="F-OTHER",
                farm_id=farm.id,
                name="Other field",
                crop=source_field.crop,
                area_ha=source_field.area_ha,
                season=2026,
                geometry=source_field.geometry,
            )
        )
        session.flush()
        report = Observation(
            field_id="F-OTHER",
            kind="field_note",
            text="Other farm's report.",
            date=date(2026, 8, 4),
            category="weeds",
            photo_jpeg=b64decode(PHOTO),
        )
        session.add(report)
        session.commit()
        observation_id = report.id
    assert client.get(f"/api/farm/observations/{observation_id}/photo").status_code == 404
    assert len(client.get("/api/farm").json()["observations"]) == 2


def test_agent_gets_only_scoped_structured_scouting_evidence(client, database) -> None:
    report = {
        "kind": "field_note",
        "text": "Private note body.",
        "category": "weeds",
        "latitude": 48.12,
        "longitude": 31.21,
        "photo_jpeg": PHOTO,
    }
    for field_id, observed_date in (
        ("F-03", "2025-06-04"),
        ("F-03", "2024-06-04"),
        ("F-01", "2025-06-04"),
    ):
        response = client.post(
            "/api/farm/observations", json={**report, "field_id": field_id, "date": observed_date}
        )
        assert response.status_code == 201

    class CaptureProvider:
        def reply(self, message: str, context: AgentContext) -> AgentAnswer:
            assert context.field is not None and isinstance(context.captured_at, datetime)
            evidence = build_field_context(
                context.field,
                season=context.season,
                locale="en",
                now=context.captured_at,
                scouting=context.scouting,
            )
            assert len(evidence["facts"]) == 1
            fact = evidence["facts"][0]
            assert fact["id"] == "f_scouting_3"
            assert fact["category"] == "weeds"
            assert fact["provenance"]["kind"] == "farmer_input"
            assert fact["provenance"]["temporal"] == {"start": "2025-06-04", "end": "2025-06-04"}
            assert "Private note body." not in str(evidence)
            assert "48.12" not in str(evidence)
            assert PHOTO not in str(evidence)
            return AgentAnswer("Recorded field input.", True, ())

    request = AgentRequest(field_id="F-03", season=2025, scenario="dry_year", message="Scouting?")
    with database() as session:
        chat(session, request, CaptureProvider())


@pytest.mark.parametrize(
    "ring",
    [
        [[0, 0], [1, 0], [1, 1], [0, 1]],
        [[0, 0], [1, 1], [0, 1], [1, 0], [0, 0]],
        [[0, 0], [1, 0], [0, 0], [0, 1], [0, 0]],
        [[181, 0], [180, 1], [180, 0], [181, 0]],
    ],
)
def test_invalid_polygon_cannot_consume_field_number(client: TestClient, ring: list) -> None:
    response = client.post(
        "/api/fields",
        json={
            "name": "Invalid field",
            "crop": "Maize",
            "area_ha": 1,
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        },
    )
    assert response.status_code == 422
    assert [field["id"] for field in client.get("/api/fields").json()] == ["F-01", "F-02", "F-03"]


def test_water_and_surface_moisture_have_independent_contracts(client: TestClient) -> None:
    water = client.get("/api/water", params={"field_id": "F-03", "scenario": "dry_year"}).json()
    surface = client.get("/api/fields/F-03/moisture").json()

    assert water["first_stress_date"] == "2026-08-03"
    assert water["min_reserve_mm"] == 58
    assert surface["depth_cm"] == [0, 5]
    assert surface["unit"] == "m³/m³"
    assert surface["demo"]
    assert surface["cells"]
    assert all(0.12 <= cell["value"] <= 0.32 for cell in surface["cells"])
    assert surface == client.get("/api/fields/F-03/moisture").json()
    assert client.get("/api/water", params={"field_id": "unknown"}).status_code == 404
    assert (
        client.get("/api/water", params={"field_id": "F-03", "scenario": "live"}).status_code == 422
    )


def test_rotation_edits_recompute_comparison_and_preserve_main_plan(client: TestClient) -> None:
    path = "/api/rotations?field_id=F-03&season=2026&scenario=dry_year"
    data = client.get(path).json()
    first, second = data["plans"]
    assert (first["stress_days"], second["stress_days"]) == (7, 4)
    assert (first["sensitive_days"], second["sensitive_days"]) == (3, 1)

    scope = {"field_id": "F-03", "season": 2026}
    assert (
        client.patch("/api/rotations/main", json={**scope, "plan_id": "plan-b"}).status_code == 200
    )
    assert (
        client.put("/api/rotations/plans/plan-b", json={**scope, "crops": ["Maize"]}).status_code
        == 422
    )
    saved = client.put(
        "/api/rotations/plans/plan-b", json={**scope, "crops": ["Maize", "Wheat", "Soy"]}
    )
    assert saved.status_code == 200
    current = client.get(path).json()
    assert current["main_plan_id"] == "plan-b"
    assert current["plans"][1]["stress_days"] == first["stress_days"]
    assert current["plans"][1]["crops"][0] == {"year": 2027, "crop": "Maize"}
    different_season = client.get("/api/rotations?field_id=F-03&season=2027").json()
    assert different_season["main_plan_id"] == "plan-a"


def test_agent_without_real_evidence_reports_missing_data_and_keeps_context(
    client: TestClient,
) -> None:
    scope = {"field_id": "F-03", "season": 2026, "scenario": "dry_year"}
    reply = client.post("/api/agent/chat", json={**scope, "message": "Explain water risk."})
    assert reply.status_code == 200
    data = reply.json()
    assert data["demo"]
    # Product change: an ordinary question must not silently create a synthetic scenario.
    assert "No current verified water forecast" in data["message"]
    assert "risk is unknown" in data["message"]
    assert "58 mm" not in data["message"]
    assert data["actions"][0]["target"] == "water"
    assert data["actions"][0]["forecast_day"] is None
    scope["conversation_id"] = data["conversation_id"]
    second = client.post("/api/agent/chat", json={**scope, "message": "Compare Plan B."})
    assert second.json()["actions"][0]["target"] == "rotations"
    wrong = client.post(
        "/api/agent/chat", json={**scope, "message": "Compare Plan B.", "scenario": "baseline"}
    )
    assert wrong.status_code == 409
    history = client.get(f"/api/agent/conversations/{scope['conversation_id']}").json()
    assert [message["role"] for message in history["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_agent_releases_transaction_and_rejects_stale_conversation_reply(
    database: sessionmaker[Session],
) -> None:
    payload = AgentRequest(field_id="F-03", season=2026, scenario="dry_year", message="First")
    with database() as session:
        initial = chat(session, payload, DemoAgentProvider())

    with database() as session:
        pending = payload.model_copy(
            update={"conversation_id": initial.conversation_id, "message": "Stale turn"}
        )

        class ConcurrentProvider:
            def reply(self, message: str, context: AgentContext) -> AgentAnswer:
                assert not session.in_transaction()
                assert [entry.role for entry in context.history] == ["user", "assistant"]
                with database() as concurrent_session:
                    other = pending.model_copy(update={"message": "Concurrent turn"})
                    chat(concurrent_session, other, DemoAgentProvider())
                return AgentAnswer(message, True, ())

        with pytest.raises(ConflictError, match="conversation changed"):
            chat(session, pending, ConcurrentProvider())
        session.rollback()
        history = conversation_detail(session, initial.conversation_id)
        assert [entry.text for entry in history.messages if entry.role == "user"] == [
            "First",
            "Concurrent turn",
        ]


def test_agent_rejects_a_completed_simulation_for_an_older_field_boundary(
    client: TestClient, database: sessionmaker[Session]
) -> None:
    field_id = "F-03"
    context = {"season": 2026, "scenario": "dry_year"}
    response = client.post(
        "/api/jobs", json={"kind": "simulation", "field_id": field_id, "inputs": context}
    )
    assert response.status_code == 202
    job_id = response.json()["id"]
    process_job(database, job_id, lease_seconds=135)
    before = client.get(f"/api/jobs/{job_id}").json()
    assert before["status"] == "succeeded" and before["boundary_revision"] == 0
    request = {**context, "field_id": field_id, "message": "Explain the run", "run_id": job_id}
    answer = client.post("/api/agent/chat", json=request)
    assert answer.status_code == 200
    assert "Demo scenario" in answer.json()["message"]
    assert "58 mm" in answer.json()["message"]
    assert answer.json()["actions"][0]["run_id"] == job_id

    field = next(item for item in client.get("/api/fields").json() if item["id"] == field_id)
    ring = [[lon, lat + 0.0001] for lon, lat in field["geometry"]["coordinates"][0]]
    assert (
        client.patch(
            f"/api/fields/{field_id}/boundary",
            json={"boundary_revision": 0, "geometry": {"type": "Polygon", "coordinates": [ring]}},
        ).status_code
        == 200
    )
    assert client.post("/api/agent/chat", json=request).status_code == 409
    assert client.get(f"/api/jobs/{job_id}").json() == before
