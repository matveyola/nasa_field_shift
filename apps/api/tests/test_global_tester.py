"""Exercise real auth/domain persistence; no provider responses or assistant mocks."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from field_shift.api import create_app
from field_shift.config import Settings
from field_shift.db import build_engine, build_session_factory
from field_shift.models import metadata
from field_shift.modules.access.models import Membership, User
from field_shift.modules.access.passwords import verify_password
from field_shift.modules.agent.models import Message
from field_shift.modules.farm.models import Farm, Observation
from field_shift.modules.fields.models import Field, FieldBoundaryVersion
from field_shift.modules.fields.planning_profile.models import ProfileSnapshot
from field_shift.modules.testing.__main__ import seed_database
from field_shift.modules.testing.cases import CASES, dataset
from field_shift.seed import seed_demo


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    url = f"sqlite:///{tmp_path / 'tester.sqlite'}"
    engine = build_engine(url)
    metadata.create_all(engine)
    yield url, root / "credentials.json", engine
    engine.dispose()


def test_seed_real_domain_idempotence_and_no_fabricated_evidence(workspace):
    url, path, engine = workspace
    assert seed_database(url, path, 2027)["outcome"] == "created"
    original = path.read_bytes()
    receipt = json.loads(original)
    assert path.stat().st_mode & 0o777 == 0o600
    assert seed_database(url, path, 2027)["outcome"] == "already_seeded"
    assert path.read_bytes() == original
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 1
        user = session.scalar(select(User))
        assert verify_password(receipt["password"], user.password_hash)
        assert session.scalar(select(func.count()).select_from(Membership)) == 1
        fields = list(session.scalars(select(Field).order_by(Field.id)))
        assert len(fields) == 24
        assert session.scalar(select(func.count()).select_from(FieldBoundaryVersion)) == 24
        for field, case in zip(fields, CASES, strict=True):
            assert field.name.startswith(f"[SYNTHETIC {case.id}]")
            assert field.geometry == case.field(2027).geometry.model_dump(mode="json")
            assert field.area_ha == case.field(2027).area_ha
        for model in (Observation, ProfileSnapshot, Message):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_no_existing_farm_or_account_adoption(workspace):
    url, path, engine = workspace
    with Session(engine) as session:
        seed_demo(session)
        before = [(f.id, f.name, f.geometry) for f in session.scalars(select(Field))]
    with pytest.raises(ValueError, match="existing|adopt"):
        seed_database(url, path, 2027)
    with Session(engine) as session:
        assert [(f.id, f.name, f.geometry) for f in session.scalars(select(Field))] == before
        assert session.scalar(select(func.count()).select_from(User)) == 0
        assert session.get(Farm, 1).name == "Oakridge Farm"


def test_receipt_cannot_adopt_same_named_account_without_password(workspace):
    url, path, engine = workspace
    seed_database(url, path, 2027)
    original = path.read_bytes()
    receipt = json.loads(original)
    receipt["password"] = "wrong-password-never-reset"
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="adopt"):
        seed_database(url, path, 2027)
    with Session(engine) as session:
        assert verify_password(
            json.loads(original)["password"], session.scalar(select(User)).password_hash
        )


def test_retry_preserves_user_edits_and_does_not_repair_missing_case(workspace):
    url, path, engine = workspace
    seed_database(url, path, 2027)
    with Session(engine) as session:
        session.get(Field, "F-01").name = "Human QA edit"
        session.commit()
    seed_database(url, path, 2027)
    with Session(engine) as session:
        assert session.get(Field, "F-01").name == "Human QA edit"
        field = session.get(Field, "F-01")
        # Change the case ID along with its history to model a supported operator edit.
        session.query(FieldBoundaryVersion).filter_by(field_id=field.id).delete()
        session.delete(field)
        session.commit()
    with pytest.raises(ValueError, match="missing"):
        seed_database(url, path, 2027)
    with Session(engine) as session:
        assert session.get(Field, "F-01") is None


def test_receipt_cannot_cross_databases_and_other_farm_is_untouched(workspace, tmp_path):
    url, path, engine = workspace
    other = f"sqlite:///{tmp_path / 'human.sqlite'}"
    other_engine = build_engine(other)
    metadata.create_all(other_engine)
    with Session(other_engine) as session:
        seed_demo(session)
    seed_database(url, path, 2027)
    with pytest.raises(ValueError, match="another"):
        seed_database(other, path, 2027)
    with Session(other_engine) as session:
        assert session.get(Farm, 1).name == "Oakridge Farm"
        assert session.scalar(select(func.count()).select_from(Field)) == 3
        assert session.scalar(select(func.count()).select_from(User)) == 0
    other_engine.dispose()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "public", "git", "relative"])
def test_unsafe_credential_paths_rejected_without_db_writes(workspace, kind):
    url, path, engine = workspace
    if kind == "symlink":
        target = path.parent / "target"
        target.write_text("untouched")
        path.symlink_to(target)
    elif kind == "hardlink":
        target = path.parent / "target"
        target.touch(mode=0o600)
        path.hardlink_to(target)
    elif kind == "public":
        path.touch(mode=0o644)
    elif kind == "git":
        (path.parent / ".git").write_text("gitdir: example")
    else:
        path = Path("credentials.json")
    with pytest.raises((ValueError, OSError)):
        seed_database(url, path, 2027)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Farm)) == 0


def test_real_team_login_and_unauthenticated_field_isolation(workspace):
    url, path, _ = workspace
    seed_database(url, path, 2027)
    receipt = json.loads(path.read_text())
    factory = build_session_factory(url)
    with TestClient(create_app(Settings(access_mode="team"), session_factory=factory)) as client:
        assert client.get("/api/fields").status_code == 401
        response = client.post(
            "/api/auth/login",
            headers={"x-field-shift-request": "1"},
            json={"email": receipt["email"], "password": receipt["password"]},
        )
        assert response.status_code == 200
        response = client.get("/api/fields")
        assert response.status_code == 200
        assert len(response.json()) == 24
        assert all(row["name"].startswith("[SYNTHETIC") for row in response.json())


def test_manifest_preserves_unknowns_and_prompts_are_not_executions():
    plan = dataset(2027)
    assert len({c["id"] for c in plan["cases"]}) == 24
    assert len({c["latitude"] > 0 for c in plan["cases"]}) == 2
    assert plan["empirical_accuracy_claim"] is False
    assert plan["provider_policy"]["fetch_on_seed"] is False
    for case in plan["cases"]:
        assert case["geometry_origin"] == "synthetic_fixture"
        assert case["land_evidence_status"] == case["soil_status"] == "missing"
        assert case["economics_status"] == "missing"
        assert case["provider_receipts"] == case["history"] == []
        assert case["expected_assistant_response"] is None
        assert case["conversation_status"] == "planned_user_prompt_only"
    assert plan["cases"][8]["field"]["geometry"] == plan["cases"][9]["field"]["geometry"]
    assert plan["cases"][10]["field"]["geometry"] == plan["cases"][11]["field"]["geometry"]


def test_cli_requires_explicit_database_without_secret_stdout(workspace):
    url, path, _ = workspace
    env = {k: v for k, v in os.environ.items() if k != "FIELD_SHIFT_TESTER_DATABASE_URL"}
    cmd = [
        sys.executable,
        "-m",
        "field_shift.modules.testing",
        "--credentials",
        str(path),
        "--season",
        "2027",
        "--confirm-isolated-database",
    ]
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert not path.exists()
    env["FIELD_SHIFT_TESTER_DATABASE_URL"] = url
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(path.read_text())
    assert receipt["password"] not in result.stdout + result.stderr
    assert receipt["bootstrap_token"] not in result.stdout + result.stderr
    assert json.loads(result.stdout)["outcome"] == "created"


def test_receipt_recovers_precommit_failure_without_partial_farm(workspace):
    url, path, engine = workspace
    # Prepare the durable identity against a missing schema, then migrate the same DB.
    metadata.drop_all(engine)
    with pytest.raises(ValueError, match="migrations"):
        seed_database(url, path, 2027)
    original = path.read_bytes()
    receipt = json.loads(original)
    metadata.create_all(engine)
    receipt["password"] = "too-short"
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        seed_database(url, path, 2027)
    with Session(engine) as session:
        for model in (Farm, Field, FieldBoundaryVersion, User, Membership):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    path.write_bytes(original)
    assert seed_database(url, path, 2027)["outcome"] == "created"


def test_forged_or_outdated_plan_receipt_never_updates_workspace(workspace):
    url, path, engine = workspace
    seed_database(url, path, 2027)
    receipt = json.loads(path.read_text())
    receipt["plan_sha256"] = "0" * 64
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="Fixture version changed"):
        seed_database(url, path, 2027)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Field)) == 24


def test_an_existing_user_without_farm_is_not_adopted(workspace):
    from datetime import UTC, datetime

    from field_shift.modules.access.passwords import password_hash

    url, path, engine = workspace
    with Session(engine) as session:
        session.add(
            User(
                email="human@example.org",
                name="Human",
                created_at=datetime.now(UTC),
                password_hash=password_hash("preserve-human-password"),
            )
        )
        session.commit()
    with pytest.raises(ValueError, match="nonempty"):
        seed_database(url, path, 2027)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Farm)) == 0
        assert session.scalar(select(User)).email == "human@example.org"


def test_concurrent_same_receipt_is_locked(workspace):
    import fcntl

    url, path, engine = workspace
    path.touch(mode=0o600)
    with path.open() as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            seed_database(url, path, 2027)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Farm)) == 0


def test_mutated_manifest_is_not_accepted_as_the_frozen_plan(workspace):
    url, path, _ = workspace
    seed_database(url, path, 2027)
    receipt = json.loads(path.read_text())
    receipt["dataset"]["cases"][0]["land_evidence_status"] = "verified"
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="Fixture version changed"):
        seed_database(url, path, 2027)


@pytest.mark.parametrize("url", ["sqlite:///:memory:", "sqlite:///relative.db"])
def test_relative_or_transient_database_refused(workspace, url):
    _, path, _ = workspace
    with pytest.raises(ValueError, match="absolute persistent"):
        seed_database(url, path, 2027)
    assert not path.exists()


def test_altered_case_field_scope_mapping_is_refused(workspace):
    url, path, _ = workspace
    seed_database(url, path, 2027)
    receipt = json.loads(path.read_text())
    receipt["field_ids"]["G07"] = "F-01"
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="Fixture version changed"):
        seed_database(url, path, 2027)
