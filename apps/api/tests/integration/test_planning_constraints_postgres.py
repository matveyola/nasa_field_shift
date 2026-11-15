"""Migration 0011 and farmer-decided planning constraints on PostgreSQL (the CI integration job)."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from time import sleep
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from field_shift.api import create_app
from field_shift.config import Settings
from field_shift.models import metadata

pytestmark = pytest.mark.integration
API_DIR = Path(__file__).resolve().parents[2]
TABLE = "field_planning_constraints"
URL = "/api/agent/constraints/F-03/2026"


@pytest.fixture
def database(monkeypatch):
    source_url = os.environ.get("FIELD_SHIFT_TEST_DATABASE_URL")
    if not source_url:
        pytest.skip("Set FIELD_SHIFT_TEST_DATABASE_URL for PostgreSQL integration tests.")
    url = make_url(source_url)
    assert url.get_backend_name() == "postgresql"
    schema = f"fs_test_{uuid4().hex}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    database_url = url.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(
        hide_password=False
    )
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(API_DIR / "alembic.ini")
    config.set_main_option("script_location", str(API_DIR / "migrations"))
    config.attributes["database_url"] = database_url
    engine = create_engine(database_url)
    try:
        command.upgrade(config, "head")
        subprocess.run(
            [sys.executable, "-m", "field_shift.seed"], cwd=API_DIR, check=True, timeout=20
        )
        yield database_url, config, engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_the_migrated_table_matches_the_model_and_reverts_cleanly(database):
    _, config, engine = database
    with engine.connect() as connection:
        diffs = compare_metadata(MigrationContext.configure(connection), metadata)

    def touches(diff) -> bool:
        return TABLE in repr(diff)

    assert [diff for diff in diffs if touches(diff)] == []
    columns = {c["name"]: c for c in inspect(engine).get_columns(TABLE)}
    assert str(columns["value"]["type"]) == "JSON" and not columns["value"]["nullable"]
    assert str(columns["decided_at"]["type"]) == "TIMESTAMP"
    assert columns["decided_at"]["type"].timezone is True
    assert inspect(engine).get_pk_constraint(TABLE)["constrained_columns"] == [
        "field_id",
        "season",
        "type",
    ]
    [foreign_key] = inspect(engine).get_foreign_keys(TABLE)
    assert foreign_key["referred_table"] == "fields"
    assert foreign_key["options"].get("ondelete") == "CASCADE"

    command.downgrade(config, "0010_precision_plans")
    assert TABLE not in inspect(engine).get_table_names()
    assert {"farms", "fields", "farm_rotation_drafts"} <= set(inspect(engine).get_table_names())
    command.upgrade(config, "head")
    assert TABLE in inspect(engine).get_table_names()


def test_decisions_round_trip_and_the_database_enforces_status_and_cascade(database):
    database_url, _, engine = database
    with engine.begin() as connection:
        irrigation = connection.execute(text("SELECT irrigation_available FROM farms")).scalar()
    confirm = {
        "field_id": "F-03",
        "envelope": {
            "id": "confirm.c1",
            "field_context_revision": 0,
            "trigger": "chip",
            "label": "Confirm",
            "action": {
                "type": "compare.set_constraints",
                "constraints": [
                    {
                        "id": "c1",
                        "type": "excluded_crops",
                        "value": ["sunflower", "maize"],
                        "status": "confirmed",
                        "source": "farmer_text",
                        "evidence": "exclude sunflower and maize",
                    },
                    {
                        "id": "c2",
                        "type": "irrigation_available",
                        "value": False,
                        "status": "confirmed",
                        "source": "agent_inferred",
                        "confidence": 0.7,
                    },
                ],
            },
        },
    }
    with TestClient(create_app(Settings(database_url=database_url))) as client:
        response = client.post(URL, json=confirm)
        assert response.status_code == 200, response.text
        listed = client.get(URL).json()["constraints"]
        context = client.get("/api/agent/assistant/F-03/2026/baseline/field-context").json()
    assert [(c["type"], c["value"]) for c in listed] == [
        ("excluded_crops", ["sunflower", "maize"]),
        ("irrigation_available", False),
    ]
    assert [(c["type"], c["source"]) for c in context["constraints"]] == [
        ("excluded_crops", "farmer_ui"),
        ("irrigation_available", "agent_inferred"),
    ]
    with engine.begin() as connection:
        assert connection.execute(text("SELECT irrigation_available FROM farms")).scalar() == (
            irrigation
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(text(f"UPDATE {TABLE} SET status = 'proposed'"))
    with engine.begin() as connection:
        field = connection.execute(text("SELECT * FROM fields WHERE id = 'F-03'")).mappings().one()
        connection.execute(
            text(
                "INSERT INTO fields (id, farm_id, name, crop, area_ha, season, geometry,"
                " boundary_revision) VALUES ('T-01', :farm, 't', 'Maize', 1, 2026,"
                " CAST(:geometry AS JSON), 0)"
            ),
            {"farm": field["farm_id"], "geometry": '{"type": "Point", "coordinates": [0, 0]}'},
        )
        connection.execute(
            text(
                f"INSERT INTO {TABLE} (field_id, season, type, boundary_revision, token, status,"
                " value, source, decided_at) VALUES ('T-01', 2026, 'organic_only', 0,"
                " gen_random_uuid(), 'confirmed', CAST('true' AS JSON), 'farmer_ui', now())"
            )
        )
        connection.execute(text("DELETE FROM fields WHERE id = 'T-01'"))
        remaining = connection.execute(
            text(f"SELECT count(*) FROM {TABLE} WHERE field_id = 'T-01'")
        ).scalar()
    assert remaining == 0


def _decision(value: bool, replaces: list | None = None) -> dict:
    body = {
        "field_id": "F-03",
        "envelope": {
            "id": "confirm.c1",
            "field_context_revision": 0,
            "trigger": "chip",
            "label": "Confirm",
            "action": {
                "type": "compare.set_constraints",
                "constraints": [
                    {
                        "id": "c1",
                        "type": "irrigation_available",
                        "value": value,
                        "status": "confirmed",
                        "source": "farmer_ui",
                    }
                ],
            },
        },
    }
    if replaces is not None:
        body["replaces"] = replaces
    return body


def test_two_tabs_replacing_the_same_reviewed_decision_cannot_both_win(database):
    database_url, _, engine = database
    app = create_app(Settings(database_url=database_url))
    with TestClient(app) as client:
        assert client.post(URL, json=_decision(False)).status_code == 200
        barrier = Barrier(2)
        [seen] = client.get(URL).json()["constraints"]
        ack = [{"type": "irrigation_available", "token": seen["token"]}]

        def replace(value: bool) -> int:
            barrier.wait()
            return client.post(URL, json=_decision(value, ack)).status_code

        with ThreadPoolExecutor(2) as pool:
            codes = sorted(pool.map(replace, (True, True)))
        listed = client.get(URL).json()["constraints"]
    # The field row lock serializes them: the second sees a new token and is refused.
    assert codes == [200, 409]
    assert [c["value"] for c in listed] == [True] and listed[0]["token"] != seen["token"]


def test_a_replacement_waits_for_the_field_lock_and_sees_the_newer_decision(database):
    """Deterministic: another transaction holds the field row lock (as a decision or boundary
    edit does) and changes the decision; the request approved against the earlier one must wait
    and then be refused, never overwrite the newer decision."""
    database_url, _, engine = database
    app = create_app(Settings(database_url=database_url))
    with TestClient(app) as client:
        assert client.post(URL, json=_decision(False)).status_code == 200
        [seen] = client.get(URL).json()["constraints"]
        ack = [{"type": "irrigation_available", "token": seen["token"]}]
        with engine.connect() as holder:
            transaction = holder.begin()
            holder.execute(text("SELECT id FROM fields WHERE id = 'F-03' FOR UPDATE"))
            with ThreadPoolExecutor(1) as pool:
                pending = pool.submit(lambda: client.post(URL, json=_decision(True, ack)))
                sleep(1)
                assert not pending.done()  # waiting on the lock, not reading the earlier one
                holder.execute(
                    text(
                        f"UPDATE {TABLE} SET token = gen_random_uuid(), status = 'rejected'"
                        " WHERE field_id = 'F-03' AND type = 'irrigation_available'"
                    )
                )
                transaction.commit()
                response = pending.result(timeout=30)
        listed = client.get(URL).json()["constraints"]
    assert response.status_code == 409
    assert [c["status"] for c in listed] == ["rejected"] and listed[0]["token"] != seen["token"]
