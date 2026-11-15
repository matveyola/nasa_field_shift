"""Upgrading to the single Alembic head from every state a real database may be in (PostgreSQL
integration job).

The states come from the actual graph, not from a list: each branch point, every revision that was a
head before a merge joined it (a head some branch shipped), and every pair of unrelated heads
applied together. Each state gets the demo data and a sample row in every branch table it already
has. Upgrading to head must keep those rows, leave one version row, be repeatable, and give exactly
the schema of a fresh install. Backing up and restoring a head database is exercised by CI's
"Restore a PostgreSQL archive" step; limits are in docs/database-migrations.md.
"""

import itertools
import json
import os
import re
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration
API_DIR = Path(__file__).resolve().parents[2]
NOW = "2026-11-15T00:00:00+00:00"
# One valid row per branch table, inserted only where the state already has that table.
SAMPLE_ROWS = {
    "field_setups": (
        "INSERT INTO field_setups (id, farm_id, field_id, inputs, created_at, updated_at)"
        " VALUES ('00000000-0000-0000-0000-000000000001', 1, 'F-03', CAST('{\"step\": 3}' AS"
        " JSON), :now, :now)"
    ),
    "field_planning_constraints": (
        "INSERT INTO field_planning_constraints"
        " (field_id, season, type, boundary_revision, token, status, value, source, decided_at)"
        " VALUES ('F-03', 2026, 'excluded_crops', 0, '00000000-0000-0000-0000-000000000002',"
        " 'confirmed',"
        " CAST('[\"sunflower\"]' AS JSON), 'farmer_ui', :now)"
    ),
    "planning_field_profiles": (
        "INSERT INTO planning_field_profiles"
        " (snapshot_id, farm_id, field_id, revision, geometry_revision, request_id, request_hash,"
        " document)"
        " VALUES ('snap-1', 1, 'F-03', 1, 0, 'req-1', repeat('a', 64), CAST('{\"crop\":"
        ' "Maize"}\' AS JSON))'
    ),
    "planning_v1_runs": (
        "INSERT INTO planning_v1_runs (id, farm_id, request_id, input_hash, payload, versions,"
        " status, attempt,"
        " created_at, updated_at) VALUES ('00000000-0000-0000-0000-000000000003', 1, 'run-req',"
        " repeat('b', 64),"
        " CAST('{}' AS JSON), CAST('{}' AS JSON), 'succeeded', 1, :now, :now)"
    ),
    "planning_input_bundles": (
        "INSERT INTO planning_input_bundles (id, farm_id, document, model_card)"
        " VALUES ('bundle-1', 1, CAST('{}' AS JSON), CAST('{\"model\": \"x\"}' AS JSON))"
    ),
}
CORE_TABLES = ("farms", "fields", "observations")


def _config(url: str | None = None) -> Config:
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(API_DIR / "migrations"))
    if url:
        config.attributes["database_url"] = url
    return config


SCRIPT = ScriptDirectory.from_config(_config())
REVISIONS = {revision.revision: revision for revision in SCRIPT.walk_revisions()}


def _parents(name: str) -> tuple[str, ...]:
    down = REVISIONS[name].down_revision
    return () if down is None else (down,) if isinstance(down, str) else tuple(down)


def _ancestors(name: str) -> set[str]:
    seen, todo = set(), list(_parents(name))
    while todo:
        current = todo.pop()
        if current not in seen:
            seen.add(current)
            todo.extend(_parents(current))
    return seen


CHILDREN = {name: [child for child in REVISIONS if name in _parents(child)] for name in REVISIONS}
BRANCH_POINTS = sorted(name for name, children in CHILDREN.items() if len(children) > 1)
SHIPPED_HEADS = sorted(
    {parent for name in REVISIONS if len(_parents(name)) > 1 for parent in _parents(name)}
)
STATES = (
    [(name,) for name in BRANCH_POINTS]
    + [(name,) for name in SHIPPED_HEADS]
    + [
        pair
        for pair in itertools.combinations(SHIPPED_HEADS, 2)
        if pair[0] not in _ancestors(pair[1]) and pair[1] not in _ancestors(pair[0])
    ]
)
HEAD = SCRIPT.get_current_head()


@pytest.fixture
def database():
    source_url = os.environ.get("FIELD_SHIFT_TEST_DATABASE_URL")
    if not source_url:
        pytest.skip("Set FIELD_SHIFT_TEST_DATABASE_URL for PostgreSQL integration tests.")
    url = make_url(source_url)
    assert url.get_backend_name() == "postgresql"
    admin = create_engine(url)
    schemas = []

    def new_schema() -> str:
        schema = f"fs_mig_{uuid4().hex}"
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        schemas.append(schema)
        return url.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(
            hide_password=False
        )

    try:
        yield new_schema
    finally:
        with admin.begin() as connection:
            for schema in schemas:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _versions(url: str) -> list[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return sorted(
                row[0]
                for row in connection.execute(text("SELECT version_num FROM alembic_version"))
            )
    finally:
        engine.dispose()


def _signature(url: str) -> dict:
    """Columns, constraints and indexes of the test schema, comparable between paths."""
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            columns = connection.execute(
                text(
                    "SELECT table_name, column_name, data_type, character_maximum_length,"
                    " is_nullable, column_default"
                    " FROM information_schema.columns WHERE table_schema = current_schema()"
                    " ORDER BY table_name, column_name"
                )
            ).all()
            constraints = connection.execute(
                text(
                    "SELECT t.relname, c.conname, c.contype, pg_get_constraintdef(c.oid) FROM"
                    " pg_constraint c"
                    " JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid ="
                    " t.relnamespace"
                    " WHERE n.nspname = current_schema() ORDER BY 1, 2"
                )
            ).all()
            indexes = connection.execute(
                text(
                    "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname ="
                    " current_schema()"
                    " ORDER BY 1, 2"
                )
            ).all()
    finally:
        engine.dispose()
    schema_name = re.compile(r"fs_mig_[0-9a-f]+\.")
    return {
        "columns": [tuple(map(str, row)) for row in columns if row[0] != "alembic_version"],
        "constraints": [tuple(map(str, row)) for row in constraints if row[0] != "alembic_version"],
        "indexes": [
            (row[0], row[1], schema_name.sub("", row[2]))
            for row in indexes
            if row[0] != "alembic_version"
        ],
    }


def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return {
                row[0]
                for row in connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
                )
            }
    finally:
        engine.dispose()


def _snapshot(url: str, tables) -> dict:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return {
                table: [
                    json.dumps(dict(row._mapping), default=str, sort_keys=True)
                    for row in connection.execute(text(f"SELECT * FROM {table} ORDER BY 1"))
                ]
                for table in sorted(tables)
            }
    finally:
        engine.dispose()


def _seed(url: str) -> list[str]:
    from field_shift.seed import seed_demo

    engine = create_engine(url)
    try:
        with Session(engine) as session:
            seed_demo(session)
        rows = [table for table in SAMPLE_ROWS if table in _tables(url)]
        with engine.begin() as connection:
            for table in rows:
                connection.execute(text(SAMPLE_ROWS[table]), {"now": NOW})
        return rows
    finally:
        engine.dispose()


def test_the_graph_has_states_to_upgrade_from():
    assert BRANCH_POINTS and SHIPPED_HEADS, "no branch to join: this test would check nothing"
    assert len(SCRIPT.get_heads()) == 1


def test_a_fresh_install_reaches_the_single_head(database):
    url = database()
    command.upgrade(_config(url), "head")
    assert _versions(url) == [HEAD]
    assert "field_planning_constraints" in _tables(
        url
    )  # the agent line is part of every integrated tree


@pytest.mark.parametrize("state", STATES, ids=lambda state: "+".join(state))
def test_every_known_state_upgrades_to_head_with_its_data_and_the_fresh_schema(database, state):
    fresh = database()
    command.upgrade(_config(fresh), "head")
    expected_schema = _signature(fresh)

    url = database()
    config = _config(url)
    for revision in state:
        command.upgrade(config, revision)
    assert _versions(url) == sorted(state)
    rows = _seed(url)
    before = _snapshot(url, set(rows) | set(CORE_TABLES))

    command.upgrade(config, "head")
    assert _versions(url) == [HEAD]
    assert _snapshot(url, set(rows) | set(CORE_TABLES)) == before
    assert _signature(url) == expected_schema

    command.upgrade(config, "head")  # a repeated deploy changes nothing
    assert _versions(url) == [HEAD]
    assert _snapshot(url, set(rows) | set(CORE_TABLES)) == before


def test_downgrading_to_the_branch_point_and_back_gives_the_same_schema(database):
    url = database()
    config = _config(url)
    command.upgrade(config, "head")
    expected = _signature(url)
    branch_point = BRANCH_POINTS[0]
    branch_tables = _tables(url)
    command.downgrade(config, branch_point)  # destroys branch data: never a production rollback
    assert _versions(url) == [branch_point]
    assert _tables(url) < branch_tables
    command.upgrade(config, "head")
    assert _signature(url) == expected
