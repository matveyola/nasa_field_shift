"""Idempotent seeding for a dedicated, initially empty database only."""

import json
from hashlib import sha256

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from field_shift.config import Settings
from field_shift.modules.access.models import Membership, User
from field_shift.modules.access.passwords import verify_password
from field_shift.modules.access.schemas import Registration
from field_shift.modules.access.service import setup
from field_shift.modules.farm.models import FARM_ID, Farm
from field_shift.modules.fields.models import Field
from field_shift.modules.fields.service import create_field

from .cases import CASES, dataset


def plan_hash(season: int) -> str:
    return sha256(json.dumps(dataset(season), sort_keys=True).encode()).hexdigest()


def seed_workspace(session: Session, receipt: dict) -> dict:
    """Caller owns an outer transaction; setup's domain commit remains inside it.

    Retry performs identity checks only. It never resets passwords, changes fields,
    backfills evidence, removes data, or adopts an existing account.
    """
    season = receipt["season"]
    expected_ids = {case.id: f"F-{i:02d}" for i, case in enumerate(CASES, 1)}
    if (
        receipt["plan_sha256"] != plan_hash(season)
        or receipt["dataset"] != dataset(season)
        or receipt["field_ids"] != expected_ids
    ):
        raise ValueError("Fixture version changed; use a new isolated database and receipt.")
    farm_name = f"SYNTHETIC global tester {receipt['seed_id']}"
    farms = list(session.scalars(select(Farm)))
    users = list(session.scalars(select(User)))
    if farms or users:
        if len(farms) != 1 or len(users) != 1:
            raise ValueError("Refusing an existing or shared workspace.")
        farm, user = farms[0], users[0]
        member = session.get(Membership, (FARM_ID, user.id))
        if (
            farm.id != FARM_ID
            or farm.name != farm_name
            or user.email != receipt["email"]
            or not verify_password(receipt["password"], user.password_hash)
            or member is None
            or member.role != "admin"
            or not member.active
            or session.scalar(select(func.count()).select_from(Membership)) != 1
        ):
            raise ValueError("Refusing to adopt an account or modify an existing farm.")
        # Existing case edits are preserved. Missing/reassigned cases require operator review,
        # not automatic recreation that could overwrite later human work.
        for index, case in enumerate(CASES, 1):
            field = session.get(Field, f"F-{index:02d}")
            if field is None or field.farm_id != FARM_ID:
                raise ValueError(f"Seeded case {case.id} is missing; no repair was performed.")
        return {"outcome": "already_seeded", "field_count": len(CASES)}
    # Foreign-key-free remnants still make a database unsafe to adopt. The CLI also
    # checks all migrated tables on first creation, before entering this function.
    if session.scalar(select(func.count()).select_from(Field)):
        raise ValueError("Refusing a database containing existing fields.")
    session.add(
        Farm(
            id=FARM_ID,
            name=farm_name,
            season=season,
            next_field_number=1,
            irrigation_available=False,
            priority="Synthetic QA: no agronomic recommendation",
            equipment=None,
            budget=None,
        )
    )
    session.flush()
    for case in CASES:
        create_field(session, case.field(season), commit=False)
    # Standard bootstrap hashes the password, creates membership and enforces the
    # existing single-farm rules. Its session token is deliberately discarded.
    setup(
        session,
        Registration(
            email=receipt["email"],
            password=receipt["password"],
            name="Synthetic global tester",
            token=receipt["bootstrap_token"],
        ),
        Settings(access_mode="team", access_bootstrap_token=receipt["bootstrap_token"]),
    )
    return {"outcome": "created", "field_count": len(CASES)}
