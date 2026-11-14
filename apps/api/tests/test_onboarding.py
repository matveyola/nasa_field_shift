from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from field_shift.api import create_app
from field_shift.config import Settings
from field_shift.db import build_session_factory
from field_shift.models import metadata
from field_shift.modules.farm.models import Farm
from field_shift.modules.fields.geometry import area_hectares
from field_shift.modules.fields.models import Field, FieldBoundaryVersion
from field_shift.modules.onboarding import service
from field_shift.modules.onboarding.models import FieldSetup
from field_shift.modules.onboarding.schemas import DemoRotationPlan, SetupInputs
from field_shift.modules.rotations.models import RotationPreference
from field_shift.seed import seed_demo

SETUP_ID = "e3121f4c-acf6-4481-82a5-5ad756187a56"
PATH = f"/api/onboarding/{SETUP_ID}"
GEOMETRY = {
    "type": "Polygon",
    "coordinates": [[[30.4, 50.2], [30.401, 50.2], [30.401, 50.201], [30.4, 50.2]]],
}
ANSWERS = {
    "country_code": "UA",
    "region_code": "UA-32",
    "name": "My field",
    "geometry": GEOMETRY,
    "history": [{"season": 2026, "crop": "Maize"}],
    "allowed_crops": ["Soy", "Wheat", "Maize"],
    "irrigation": False,
    "priority": "soil",
}


@pytest.fixture
def database(tmp_path) -> Iterator[sessionmaker[Session]]:
    factory = build_session_factory(f"sqlite:///{(tmp_path / 'onboarding.sqlite').as_posix()}")
    engine = factory.kw["bind"]
    metadata.create_all(engine)
    with factory() as session:
        seed_demo(session)
    yield factory
    engine.dispose()


@pytest.fixture
def client(database) -> Iterator[TestClient]:
    with TestClient(create_app(Settings(access_mode="local"), session_factory=database)) as client:
        yield client


def test_draft_resumes_and_distinguishes_unanswered_from_unknown(client):
    assert client.get(PATH).status_code == 404
    response = client.put(PATH, json={"country_code": "UA"})
    assert response.status_code == 200
    draft = response.json()
    assert draft["status"] == "draft"
    assert draft["field"] is None and draft["demo_plan"] is None
    assert "irrigation" not in draft["inputs"] and "history" not in draft["inputs"]
    assert client.get(PATH).json() == draft
    assert client.post(PATH + "/complete").status_code == 409
    assert len(client.get("/api/fields").json()) == 3

    unknown = {**ANSWERS, "history": [], "irrigation": None}
    assert client.put(PATH, json=unknown).status_code == 200
    completed = client.post(PATH + "/complete").json()
    assert completed["status"] == "completed"
    assert completed["field"]["crop"] == "Unknown"
    assert completed["inputs"]["history"] == []
    assert completed["inputs"]["irrigation"] is None


def test_complete_is_atomic_idempotent_and_does_not_write_real_rotation(client, database):
    assert client.put(PATH, json=ANSWERS).status_code == 200
    response = client.post(PATH + "/complete")
    assert response.status_code == 200, response.text
    saved = response.json()
    field_id = saved["field"]["id"]
    assert saved["field"]["area_ha"] == pytest.approx(area_hectares(GEOMETRY["coordinates"][0]))
    assert saved["field"]["crop"] == "Maize"
    assert saved["demo_plan"]["demo"] is True
    assert saved["demo_plan"]["source"] == "onboarding_demo_v1"
    crops = saved["demo_plan"]["crops"]
    assert [crop["year"] for crop in crops] == [2027, 2028, 2029]
    assert all(crop["crop"] in ANSWERS["allowed_crops"] for crop in crops)
    assert crops[0]["crop"] != "Maize"
    assert all(a["crop"] != b["crop"] for a, b in zip(crops, crops[1:]))
    assert client.post(PATH + "/complete").json() == saved
    assert client.put(PATH, json=ANSWERS).json() == saved
    assert client.get(f"/api/onboarding/fields/{field_id}").json() == saved
    assert client.put(PATH, json={**ANSWERS, "name": "Different"}).status_code == 409
    assert len(client.get("/api/fields").json()) == 4
    with database() as session:
        assert session.scalar(select(func.count()).select_from(FieldSetup)) == 1
        assert session.scalar(select(func.count()).select_from(RotationPreference)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(FieldBoundaryVersion)
                .where(FieldBoundaryVersion.field_id == field_id)
            )
            == 1
        )


def test_another_setup_adds_an_independent_field_and_keeps_existing_fields(client):
    before = client.get("/api/fields").json()
    saved = []
    for name in ("North", "South"):
        path = f"/api/onboarding/{uuid4()}"
        client.put(path, json={**ANSWERS, "name": name})
        saved.append(client.post(path + "/complete").json())
    assert saved[0]["field"]["id"] != saved[1]["field"]["id"]
    assert client.get("/api/fields").json()[:3] == before
    assert len(client.get("/api/fields").json()) == 5
    assert client.get("/api/onboarding/fields/F-01").status_code == 404


def test_old_crop_history_is_not_misrepresented_as_current_crop_or_boundary(client, database):
    client.put(PATH, json={**ANSWERS, "history": [{"season": 2024, "crop": "Soy"}]})
    saved = client.post(PATH + "/complete").json()
    assert saved["field"]["crop"] == "Unknown"
    assert saved["inputs"]["history"] == [{"season": 2024, "crop": "Soy"}]
    with database() as session:
        field = session.get(Field, saved["field"]["id"])
        assert field.season == 2026
        boundary = session.scalar(
            select(FieldBoundaryVersion).where(FieldBoundaryVersion.field_id == field.id)
        )
        assert boundary.effective_season == 2026


@pytest.mark.parametrize(
    "allowed,preceding,expected_count",
    [
        (["Soy", "Wheat"], None, 2),
        (["Soy", "Wheat"], "Soy", 1),
        (["Soy", "Wheat", "Maize"], "Maize", 3),
        (["Soy", "Wheat", "Maize", "Peas"], "Wheat", 3),
    ],
)
def test_demo_alternatives_are_distinct_feasible_and_bounded(allowed, preceding, expected_count):
    inputs = SetupInputs.model_validate(
        {
            **ANSWERS,
            "start_season": 2027,
            "allowed_crops": allowed,
            "history": [{"season": 2026, "crop": preceding}],
        }
    )
    plan = service._demo_plan(inputs)
    sequences = [plan.crops, *plan.alternatives]
    assert len(sequences) == expected_count
    assert len({tuple(crop.crop for crop in sequence) for sequence in sequences}) == expected_count
    for sequence in sequences:
        assert [crop.year for crop in sequence] == [2027, 2028, 2029]
        names = [crop.crop for crop in sequence]
        assert set(names) <= set(allowed)
        assert names[0] != preceding
        assert all(a != b for a, b in zip(names, names[1:]))
    assert plan == service._demo_plan(inputs)


def test_demo_alternatives_preserve_primary_and_accept_older_snapshots():
    plan = service._demo_plan(SetupInputs.model_validate({**ANSWERS, "start_season": 2027}))
    assert [crop.crop for crop in plan.crops] == ["Soy", "Wheat", "Maize"]
    legacy = plan.model_dump(mode="json", exclude={"alternatives"})
    assert DemoRotationPlan.model_validate(legacy).alternatives == []


def test_simultaneous_sqlite_completion_returns_one_persisted_field(client, database):
    assert client.put(PATH, json=ANSWERS).status_code == 200
    barrier = Barrier(3)

    def complete(_):
        with TestClient(
            create_app(Settings(access_mode="local"), session_factory=database),
            raise_server_exceptions=False,
        ) as other:
            barrier.wait(timeout=10)
            response = other.post(PATH + "/complete")
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=3) as executor:
        responses = list(executor.map(complete, range(3)))
    assert [status for status, _ in responses] == [200, 200, 200]
    assert responses[0][1] == responses[1][1] == responses[2][1]
    saved = responses[0][1]
    assert client.post(PATH + "/complete").json() == saved
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Field)) == 4
        assert session.scalar(select(func.count()).select_from(FieldBoundaryVersion)) == 1
        assert session.get(FieldSetup, SETUP_ID).field_id == saved["field"]["id"]
        assert session.get(Farm, 1).next_field_number == 5


def test_sqlite_busy_response_can_be_replayed_without_duplicate_or_orphan(client, database):
    assert client.put(PATH, json=ANSWERS).status_code == 200
    engine = database.kw["bind"]

    def short_timeout(connection, _record, _proxy):
        connection.execute("PRAGMA busy_timeout=20")

    event.listen(engine, "checkout", short_timeout)
    try:
        with engine.begin() as connection:
            connection.execute(
                update(Farm).where(Farm.id == 1).values(next_field_number=Farm.next_field_number)
            )
            response = client.post(PATH + "/complete")
            assert response.status_code == 409
            assert "in progress" in response.json()["detail"]
        response = client.post(PATH + "/complete")
        assert response.status_code == 200
        assert client.post(PATH + "/complete").json() == response.json()
        with database() as session:
            assert session.scalar(select(func.count()).select_from(Field)) == 4
            assert session.scalar(select(func.count()).select_from(FieldBoundaryVersion)) == 1
    finally:
        event.remove(engine, "checkout", short_timeout)


@pytest.mark.parametrize(
    "changes,status",
    [
        ({"allowed_crops": ["Soy", "Soy"]}, 422),
        ({"history": [{"season": 2026, "crop": "Soy"}] * 2}, 422),
        ({"history": [{"season": 2027, "crop": "Soy"}]}, 409),
        ({"start_season": 2025}, 422),
        ({"country_code": "Ukraine"}, 422),
        ({"priority": "yield"}, 422),
        ({"unexpected": "value"}, 422),
    ],
)
def test_invalid_answers_do_not_create_fields(client, changes, status):
    assert client.put(PATH, json={**ANSWERS, **changes}).status_code == status
    assert len(client.get("/api/fields").json()) == 3


def test_failed_completion_rolls_back_field_number_and_boundary(client, database, monkeypatch):
    client.put(PATH, json=ANSWERS)

    def fail(_inputs):
        raise RuntimeError("Unexpected plan generation error")

    monkeypatch.setattr(service, "_demo_plan", fail)
    with database() as session, pytest.raises(RuntimeError):
        service.complete_setup(session, UUID(SETUP_ID))
    with database() as session:
        assert session.get(FieldSetup, SETUP_ID).field_id is None
        assert session.get(Farm, 1).next_field_number == 4
        assert session.get(Field, "F-04") is None
        assert session.scalar(select(func.count()).select_from(FieldBoundaryVersion)) == 0


def test_other_farm_setup_is_not_readable_or_replaceable(client, database):
    client.put(PATH, json=ANSWERS)
    with database() as session:
        session.add(
            Farm(
                id=2,
                name="Other",
                season=2026,
                next_field_number=1,
                irrigation_available=False,
                priority="Water",
            )
        )
        session.flush()
        session.get(FieldSetup, SETUP_ID).farm_id = 2
        session.commit()
    assert client.get(PATH).status_code == 404
    assert client.put(PATH, json=ANSWERS).status_code == 404
    assert client.post(PATH + "/complete").status_code == 404


def test_team_access_guards_setup_reads_and_writes(database):
    with TestClient(create_app(Settings(access_mode="team"), session_factory=database)) as client:
        assert client.get(PATH).status_code == 401
        assert (
            client.put(PATH, json=ANSWERS, headers={"X-Field-Shift-Request": "1"}).status_code
            == 401
        )
        assert (
            client.post(PATH + "/complete", headers={"X-Field-Shift-Request": "1"}).status_code
            == 401
        )
