from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from field_shift.api import create_app
from field_shift.config import Settings
from field_shift.db import build_engine, build_session_factory
from field_shift.jobs.models import JobKind
from field_shift.jobs.repository import submit
from field_shift.models import metadata
from field_shift.modules.access import service
from field_shift.modules.access.models import Invitation, LoginSession, Membership, User
from field_shift.modules.access.passwords import digest_token, password_hash, verify_password
from field_shift.modules.access.recover import reset_password
from field_shift.modules.access.schemas import InviteCreate, MemberUpdate
from field_shift.modules.agent.models import Conversation
from field_shift.modules.farm.models import Farm, Observation
from field_shift.modules.fields.models import Field
from field_shift.seed import seed_demo

TOKEN = "test-bootstrap-token-32-characters-long"
PASSWORD = "test-password-never-for-real-deployment"
HEADERS = {"X-Field-Shift-Request": "1"}


@pytest.fixture
def workspace(tmp_path):
    factory = build_session_factory(f"sqlite:///{tmp_path / 'access.sqlite'}")
    metadata.create_all(factory.kw["bind"])
    with factory() as session:
        seed_demo(session)
    settings = Settings(access_mode="team", access_bootstrap_token=TOKEN)
    app = create_app(settings, session_factory=factory)
    with TestClient(app, headers=HEADERS) as client:
        yield client, factory, app
    factory.kw["bind"].dispose()


def setup(client):
    response = client.post(
        "/api/auth/setup",
        json={"email": "admin@example.org", "name": "Admin", "password": PASSWORD, "token": TOKEN},
    )
    assert response.status_code == 200, response.text
    state = client.get("/api/auth/session").json()
    client.headers["X-CSRF-Token"] = state["csrf_token"]
    return state


def add_member(client, app, role="viewer", email="viewer@example.org"):
    invitation = client.post("/api/team/invitations", json={"email": email, "role": role})
    assert invitation.status_code == 201, invitation.text
    other = TestClient(app, headers=HEADERS)
    response = other.post(
        "/api/auth/accept",
        json={
            "name": "Member",
            "email": email,
            "password": PASSWORD,
            "token": invitation.json()["token"],
        },
    )
    assert response.status_code == 200, response.text
    state = other.get("/api/auth/session").json()
    other.headers["X-CSRF-Token"] = state["csrf_token"]
    return other, state


def test_local_mode_is_explicit_and_does_not_enable_team_mutations(workspace):
    _, factory, _ = workspace
    with TestClient(create_app(Settings(access_mode="local"), session_factory=factory)) as client:
        state = client.get("/api/auth/session").json()
        assert state["mode"] == "local" and state["role"] == "admin"
        assert client.get("/api/fields").status_code == 200
        assert (
            client.post(
                "/api/team/invitations", json={"email": "a@example.org", "role": "admin"}
            ).status_code
            == 409
        )
        assert (
            client.patch(
                "/api/farm/defaults",
                json={"budget": 99},
                headers={"Origin": "https://other.example"},
            ).status_code
            == 403
        )


@pytest.mark.parametrize(
    "path",
    [
        "/api/fields",
        "/api/farm",
        "/api/jobs",
        "/api/agent/status",
        "/api/farm/observations/1/photo",
        "/api/planning/drafts",
        "/api/fields/F-03/precision?season=2026",
        "/openapi.json",
    ],
)
def test_team_mode_guards_every_data_surface(workspace, path):
    client, _, _ = workspace
    assert client.get(path).status_code == 401
    assert client.get("/api/health").status_code == 200


def test_setup_is_one_time_and_password_and_tokens_are_not_stored_raw(workspace):
    client, factory, _ = workspace
    state = setup(client)
    assert state["role"] == "admin" and state["needs_setup"] is False
    assert (
        client.post(
            "/api/auth/setup",
            json={
                "email": "second@example.org",
                "name": "Admin",
                "password": PASSWORD,
                "token": TOKEN,
            },
        ).status_code
        == 409
    )
    with factory() as db:
        user = db.scalar(select(User))
        saved = db.scalar(select(LoginSession))
        assert user.password_hash != PASSWORD and verify_password(PASSWORD, user.password_hash)
        assert saved.token_hash == digest_token(client.cookies.get("fs_session"))
        assert state["csrf_token"] != client.cookies.get("fs_session")
    raw = client.cookies.get("fs_session")
    assert client.post("/api/auth/logout", json={}).status_code == 200
    assert client.get("/api/fields").status_code == 401
    client.cookies.set("fs_session", raw, path="/api")
    assert client.get("/api/fields").status_code == 401


def test_csrf_origin_and_role_fail_closed(workspace):
    client, _, app = workspace
    setup(client)
    payload = {"budget": 123}
    assert (
        client.patch(
            "/api/farm/defaults", json=payload, headers={"X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )
    assert (
        client.patch(
            "/api/farm/defaults", json=payload, headers={"Origin": "https://attacker.example"}
        ).status_code
        == 403
    )
    assert (
        client.patch(
            "/api/farm/defaults", json=payload, headers={"Sec-Fetch-Site": "cross-site"}
        ).status_code
        == 403
    )
    assert client.patch("/api/farm/defaults", json=payload).status_code == 200
    viewer, _ = add_member(client, app)
    with viewer:
        assert viewer.get("/api/fields").status_code == 200
        assert viewer.patch("/api/farm/defaults", json=payload).status_code == 403
        assert viewer.post("/api/agent/chat", json={}).status_code == 403
        assert viewer.post("/api/jobs", json={}).status_code == 403
        assert viewer.post("/api/fields/F-03/precision/trials", json={}).status_code == 403
        assert viewer.post("/api/auth/logout", json={}).status_code == 200


def test_editor_cannot_manage_team_and_downgrade_revocation_apply_immediately(workspace):
    client, _, app = workspace
    setup(client)
    editor, state = add_member(client, app, role="editor", email="editor@example.org")
    with editor:
        assert editor.patch("/api/farm/defaults", json={"budget": 12}).status_code == 200
        assert (
            editor.post(
                "/api/team/invitations", json={"email": "b@example.org", "role": "admin"}
            ).status_code
            == 403
        )
        member_path = "/api/team/members/" + str(state["user"]["id"])
        assert client.patch(member_path, json={"role": "viewer", "active": True}).status_code == 200
        assert editor.patch("/api/farm/defaults", json={"budget": 13}).status_code == 403
        assert (
            client.patch(member_path, json={"role": "viewer", "active": False}).status_code == 200
        )
        assert editor.get("/api/fields").status_code == 401
        assert client.patch(member_path, json={"role": "editor", "active": True}).status_code == 200
        assert editor.get("/api/fields").status_code == 401


def test_last_admin_cannot_be_removed_or_demoted(workspace):
    client, _, _ = workspace
    state = setup(client)
    path = "/api/team/members/" + str(state["user"]["id"])
    assert client.patch(path, json={"role": "viewer", "active": True}).status_code == 409
    assert client.patch(path, json={"role": "admin", "active": False}).status_code == 409
    assert client.get("/api/auth/session").json()["role"] == "admin"


def test_admin_mutations_recheck_role_inside_the_farm_lock(workspace):
    client, factory, app = workspace
    setup(client)
    second, state = add_member(client, app, role="admin", email="admin2@example.org")
    actor_id = state["user"]["id"]
    with second:
        with factory() as db:
            old_actor = service.current_principal(db, second.cookies.get("fs_session"))
            assert old_actor.role == "admin"
        assert (
            client.patch(
                f"/api/team/members/{actor_id}", json={"role": "viewer", "active": True}
            ).status_code
            == 200
        )
        with factory() as db:
            with pytest.raises(HTTPException) as denied:
                service.invite(
                    db, InviteCreate(email="attempt@example.org", role="admin"), old_actor.user_id
                )
            assert denied.value.status_code == 403
            db.rollback()
            with pytest.raises(HTTPException):
                service.update_member(
                    db, 1, MemberUpdate(role="viewer", active=False), old_actor.user_id
                )


def test_invitation_requires_intended_email_expires_revokes_and_is_one_use(workspace):
    client, factory, app = workspace
    setup(client)
    created = client.post(
        "/api/team/invitations", json={"email": "invite@example.org", "role": "editor"}
    ).json()
    body = {
        "email": "wrong@example.org",
        "name": "Invited",
        "password": PASSWORD,
        "token": created["token"],
    }
    with TestClient(app, headers=HEADERS) as other:
        assert other.post("/api/auth/accept", json=body).status_code == 403
        body["email"] = "invite@example.org"
        assert other.post("/api/auth/accept", json=body).status_code == 200
        assert other.post("/api/auth/accept", json=body).status_code == 403
    for expiry in (False, True):
        created = client.post(
            "/api/team/invitations", json={"email": f"test{expiry}@example.org", "role": "viewer"}
        ).json()
        body.update(email=created["email"], token=created["token"])
        if expiry:
            with factory() as db:
                db.get(Invitation, created["id"]).expires_at = datetime.now(UTC) - timedelta(
                    seconds=1
                )
                db.commit()
        else:
            assert client.delete("/api/team/invitations/" + created["id"]).status_code == 200
        with TestClient(app, headers=HEADERS) as other:
            assert other.post("/api/auth/accept", json=body).status_code == 403


def test_cross_farm_jobs_photos_and_conversations_are_never_exposed(workspace):
    client, factory, _ = workspace
    setup(client)
    with factory() as db:
        own = db.get(Field, "F-03")
        db.add(
            Farm(
                id=2,
                name="Other",
                season=2026,
                next_field_number=1,
                irrigation_available=False,
                priority="Private",
            )
        )
        db.flush()
        db.add(
            Field(
                id="OTHER",
                farm_id=2,
                name="Private",
                crop="Maize",
                area_ha=2,
                season=2026,
                geometry=own.geometry,
            )
        )
        db.flush()
        other_job = submit(db, kind=JobKind.GEO_ML, field_id="OTHER", inputs={})
        observation = Observation(
            field_id="OTHER",
            kind="field_note",
            text="Private",
            date=datetime.now(UTC).date(),
            photo_jpeg=b"photo",
        )
        conversation = Conversation(
            id=uuid4(),
            field_id="OTHER",
            season=2026,
            scenario="baseline",
            title="Private",
            updated_at=datetime.now(UTC),
        )
        db.add_all([observation, conversation])
        db.commit()
        job_id, photo_id, conversation_id = other_job.id, observation.id, conversation.id
    assert client.get("/api/jobs/" + job_id).status_code == 404
    assert client.get("/api/jobs?field_id=OTHER").status_code == 404
    assert all(item["field_id"] != "OTHER" for item in client.get("/api/jobs").json())
    assert client.get(f"/api/farm/observations/{photo_id}/photo").status_code == 404
    assert client.get(f"/api/agent/conversations/{conversation_id}").status_code == 404
    assert all(item["id"] != "OTHER" for item in client.get("/api/fields").json())


def test_expired_session_and_foreign_farm_membership_are_rejected(workspace):
    client, factory, _ = workspace
    setup(client)
    with factory() as db:
        entry = db.scalar(select(LoginSession))
        entry.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/farm").status_code == 401


def test_password_change_revokes_other_sessions_and_rotates_current(workspace):
    client, _, app = workspace
    setup(client)
    with TestClient(app, headers=HEADERS) as other:
        assert (
            other.post(
                "/api/auth/login", json={"email": "admin@example.org", "password": PASSWORD}
            ).status_code
            == 200
        )
        old = client.cookies.get("fs_session")
        assert (
            client.post(
                "/api/auth/password",
                json={
                    "current_password": PASSWORD,
                    "new_password": "changed-password-is-long-enough",
                },
            ).status_code
            == 200
        )
        assert client.cookies.get("fs_session") != old
        assert other.get("/api/farm").status_code == 401
        assert client.get("/api/farm").status_code == 200


def test_auth_throttle_persists_between_clients_and_hashes_have_unique_salts(workspace):
    client, factory, app = workspace
    with factory() as db:
        for _ in range(10):
            service.throttle(db, "auth-account:blocked@example.org")
        with pytest.raises(HTTPException) as error:
            service.throttle(db, "auth-account:blocked@example.org")
        assert error.value.status_code == 429
    with TestClient(app, headers=HEADERS) as other:
        response = other.post(
            "/api/auth/login", json={"email": "blocked@example.org", "password": PASSWORD}
        )
        assert response.status_code == 429
    first, second = password_hash(PASSWORD), password_hash(PASSWORD)
    assert first != second and verify_password(PASSWORD, first)
    assert not verify_password("incorrect-password", first)
    assert not verify_password(PASSWORD, "broken")


def test_production_rejects_unprotected_configuration():
    with pytest.raises(ValueError):
        Settings(environment="production", access_mode="local")
    with pytest.raises(ValueError):
        Settings(environment="production", access_mode="team", access_secure_cookie=False)
    settings = Settings(
        environment="production",
        access_mode="team",
        access_secure_cookie=True,
        access_public_origin="https://farm.example.org",
    )
    assert settings.access_mode == "team"
    with pytest.raises(ValueError):
        Settings(environment="prod")
    with pytest.raises(ValueError):
        Settings(access_public_origin="https://user:password@example.org/path")


def test_secure_cookie_and_public_origin_keep_port_and_reject_other_hosts(workspace):
    _, factory, _ = workspace
    settings = Settings(
        access_mode="team",
        access_bootstrap_token=TOKEN,
        access_public_origin="https://farm.example.org:8443",
        access_secure_cookie=True,
    )
    with TestClient(
        create_app(settings, session_factory=factory),
        base_url="https://farm.example.org:8443",
        headers=HEADERS,
    ) as client:
        response = client.post(
            "/api/auth/setup",
            json={
                "email": "admin@example.org",
                "name": "Admin",
                "password": PASSWORD,
                "token": TOKEN,
            },
            headers={"Origin": "https://farm.example.org:8443"},
        )
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
        assert "path=/api" in cookie
        assert client.get("/api/fields", headers={"Host": "wrong.example"}).status_code == 400
        assert (
            client.post(
                "/api/auth/login",
                json={"email": "admin@example.org", "password": PASSWORD},
                headers={"Origin": "https://farm.example.org"},
            ).status_code
            == 403
        )


def test_public_auth_requires_custom_header_and_operator_recovery_revokes_sessions(workspace):
    client, factory, app = workspace
    setup(client)
    with TestClient(app) as other:
        assert (
            other.post(
                "/api/auth/login", json={"email": "admin@example.org", "password": PASSWORD}
            ).status_code
            == 403
        )
    with factory() as db:
        reset_password(db, "admin@example.org", "recovered-password-is-long")
        assert db.get(Membership, (1, 1)).role == "admin"
    assert client.get("/api/farm").status_code == 401
    assert (
        client.post(
            "/api/auth/login",
            json={"email": "admin@example.org", "password": "recovered-password-is-long"},
        ).status_code
        == 200
    )


def test_account_from_another_farm_cannot_open_this_workspace(workspace):
    client, factory, _ = workspace
    with factory() as db:
        db.add(
            Farm(
                id=2,
                name="Other",
                season=2026,
                next_field_number=1,
                irrigation_available=False,
                priority="Private",
            )
        )
        user = User(
            email="other@example.org",
            name="Other",
            password_hash=password_hash(PASSWORD),
            created_at=datetime.now(UTC),
        )
        db.add(user)
        db.flush()
        db.add(Membership(farm_id=2, user_id=user.id, role="admin", active=True))
        raw = "foreign-session-random-token-for-test-only"
        db.add(
            LoginSession(
                token_hash=digest_token(raw),
                user_id=user.id,
                farm_id=2,
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        db.commit()
    client.cookies.set("fs_session", raw, path="/api")
    assert client.get("/api/fields").status_code == 401
    assert (
        client.post(
            "/api/auth/login", json={"email": "other@example.org", "password": PASSWORD}
        ).status_code
        == 401
    )


def test_access_migration_preserves_existing_farm(tmp_path):
    root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'migrate.sqlite').as_posix()}"
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0007_field_boundary_history")
    factory = build_session_factory(url)
    with factory() as db:
        seed_demo(db)
    command.upgrade(config, "0008_team_access")
    with factory() as db:
        assert db.get(Farm, 1).name == "Oakridge Farm"
        assert db.scalars(select(User)).all() == []
    command.downgrade(config, "0007_field_boundary_history")
    engine = build_engine(url)
    assert "access_users" not in __import__("sqlalchemy").inspect(engine).get_table_names()
    engine.dispose()
    factory.kw["bind"].dispose()
