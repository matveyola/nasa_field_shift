from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from secrets import compare_digest, token_urlsafe
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from field_shift.config import Settings
from field_shift.modules.access.models import (
    AccessThrottle,
    Invitation,
    LoginSession,
    Membership,
    User,
)
from field_shift.modules.access.passwords import (
    csrf_token,
    digest_token,
    password_hash,
    verify_password,
)
from field_shift.modules.access.schemas import Credentials, InviteCreate, MemberUpdate, Registration
from field_shift.modules.farm.models import FARM_ID, Farm

SESSION_SECONDS = 8 * 60 * 60


def now() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


@dataclass(frozen=True)
class Principal:
    user_id: int | None
    farm_id: int
    name: str
    email: str | None
    role: str
    csrf: str | None = None


def current_principal(session: Session, raw_token: str | None) -> Principal | None:
    if raw_token is None or not 32 <= len(raw_token) <= 128:
        return None
    row = session.execute(
        select(LoginSession, User, Membership)
        .join(User, User.id == LoginSession.user_id)
        .join(
            Membership,
            (Membership.user_id == LoginSession.user_id)
            & (Membership.farm_id == LoginSession.farm_id),
        )
        .where(
            LoginSession.token_hash == digest_token(raw_token),
            LoginSession.farm_id == FARM_ID,
            LoginSession.expires_at > now(),
            Membership.active.is_(True),
        )
    ).one_or_none()
    if row is None:
        return None
    _, user, member = row
    return Principal(
        user.id, member.farm_id, user.name, user.email, member.role, csrf_token(raw_token)
    )


def lock_farm(session: Session) -> None:
    if session.scalar(select(Farm.id).where(Farm.id == FARM_ID).with_for_update()) is None:
        raise HTTPException(503, "Farm is not initialized.")


def lock_admin(session: Session, actor_id: int | None) -> None:
    lock_farm(session)
    actor = session.get(Membership, (FARM_ID, actor_id), populate_existing=True)
    if actor is None or not actor.active or actor.role != "admin":
        raise HTTPException(403, "Administrator access is required.")


def throttle(session: Session, identity: str, *, limit: int = 10, seconds: int = 900) -> None:
    """Database-backed limit shared by API processes; no Redis dependency for sign-in."""
    lock_farm(session)
    at = now()
    key = digest_token(identity)
    entry = session.get(AccessThrottle, key)
    if entry is None:
        session.add(AccessThrottle(key=key, window_start=at, attempts=1))
    elif aware(entry.window_start) <= at - timedelta(seconds=seconds):
        entry.window_start, entry.attempts = at, 1
    elif entry.attempts >= limit:
        session.rollback()
        raise HTTPException(
            429, "Too many requests. Try again later.", headers={"Retry-After": str(seconds)}
        )
    else:
        entry.attempts += 1
    session.execute(
        delete(AccessThrottle)
        .where(AccessThrottle.window_start < at - timedelta(days=1))
        .execution_options(synchronize_session=False)
    )
    session.commit()


def has_admin(session: Session) -> bool:
    return bool(
        session.scalar(
            select(Membership.user_id)
            .where(
                Membership.farm_id == FARM_ID,
                Membership.role == "admin",
                Membership.active.is_(True),
            )
            .limit(1)
        )
    )


def issue_session(session: Session, user: User) -> str:
    raw_token = token_urlsafe(32)
    at = now()
    session.execute(
        delete(LoginSession)
        .where(LoginSession.expires_at <= at)
        .execution_options(synchronize_session=False)
    )
    previous = list(
        session.scalars(
            select(LoginSession)
            .where(LoginSession.user_id == user.id, LoginSession.farm_id == FARM_ID)
            .order_by(LoginSession.created_at.desc())
        )
    )
    for old in previous[4:]:
        session.delete(old)
    session.add(
        LoginSession(
            token_hash=digest_token(raw_token),
            user_id=user.id,
            farm_id=FARM_ID,
            created_at=at,
            expires_at=at + timedelta(seconds=SESSION_SECONDS),
        )
    )
    session.commit()
    return raw_token


def setup(session: Session, body: Registration, settings: Settings) -> str:
    if not settings.access_bootstrap_token or not compare_digest(
        digest_token(body.token), digest_token(settings.access_bootstrap_token)
    ):
        raise HTTPException(403, "The setup token is invalid.")
    lock_farm(session)
    # Never reopen bootstrap when an account is disabled or its role changes.
    if session.scalar(
        select(func.count()).select_from(Membership).where(Membership.farm_id == FARM_ID)
    ):
        raise HTTPException(409, "Team access has already been configured.")
    if session.scalar(select(User.id).where(User.email == body.email)):
        raise HTTPException(409, "This account already exists.")
    user = User(
        email=body.email,
        name=body.name,
        password_hash=password_hash(body.password),
        created_at=now(),
    )
    session.add(user)
    session.flush()
    session.add(Membership(farm_id=FARM_ID, user_id=user.id, role="admin", active=True))
    return issue_session(session, user)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return password_hash("not-a-real-account-password")


def login(session: Session, body: Credentials) -> str:
    lock_farm(session)
    user = session.scalar(select(User).where(User.email == body.email))
    valid = verify_password(body.password, user.password_hash if user else _dummy_hash())
    member = session.get(Membership, (FARM_ID, user.id)) if user else None
    if not valid or member is None or not member.active:
        raise HTTPException(401, "Email or password is incorrect.")
    return issue_session(session, user)


def invite(session: Session, body: InviteCreate, actor_id: int | None) -> dict:
    lock_admin(session, actor_id)
    existing = session.scalar(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(User.email == body.email, Membership.farm_id == FARM_ID, Membership.active.is_(True))
    )
    if existing:
        raise HTTPException(409, "This person is already a team member.")
    at = now()
    pending = session.scalars(
        select(Invitation).where(
            Invitation.farm_id == FARM_ID,
            Invitation.email == body.email,
            Invitation.consumed_at.is_(None),
        )
    )
    for previous in pending:
        previous.consumed_at = at
    raw_token = token_urlsafe(32)
    invitation = Invitation(
        id=str(uuid4()),
        farm_id=FARM_ID,
        email=body.email,
        role=body.role,
        token_hash=digest_token(raw_token),
        created_at=at,
        expires_at=at + timedelta(days=7),
    )
    session.add(invitation)
    session.commit()
    return {
        "id": invitation.id,
        "token": raw_token,
        "email": body.email,
        "role": body.role,
        "expires_at": invitation.expires_at,
    }


def accept(session: Session, body: Registration) -> str:
    lock_farm(session)
    invitation = session.scalar(
        select(Invitation)
        .where(
            Invitation.token_hash == digest_token(body.token),
            Invitation.farm_id == FARM_ID,
            Invitation.consumed_at.is_(None),
            Invitation.expires_at > now(),
            Invitation.email == body.email,
        )
        .with_for_update()
    )
    if invitation is None:
        raise HTTPException(403, "The invitation is invalid or has expired.")
    user = session.scalar(select(User).where(User.email == body.email))
    if user is None:
        user = User(
            email=body.email,
            name=body.name,
            password_hash=password_hash(body.password),
            created_at=now(),
        )
        session.add(user)
        session.flush()
    elif not verify_password(body.password, user.password_hash):
        # A new invitation may restore access, but must never replace an existing password.
        raise HTTPException(401, "Use the existing account password to accept this invitation.")
    member = session.get(Membership, (FARM_ID, user.id))
    if member and member.active:
        raise HTTPException(409, "This person is already a team member.")
    if member is None:
        session.add(Membership(farm_id=FARM_ID, user_id=user.id, role=invitation.role, active=True))
    else:
        member.role, member.active = invitation.role, True
    invitation.consumed_at = now()
    return issue_session(session, user)


def update_member(session: Session, user_id: int, body: MemberUpdate, actor_id: int | None) -> None:
    lock_admin(session, actor_id)
    member = session.get(Membership, (FARM_ID, user_id))
    if member is None:
        raise HTTPException(404, "Team member was not found.")
    if member.active and member.role == "admin" and (not body.active or body.role != "admin"):
        admins = session.scalar(
            select(func.count())
            .select_from(Membership)
            .where(
                Membership.farm_id == FARM_ID,
                Membership.role == "admin",
                Membership.active.is_(True),
            )
        )
        if admins == 1:
            raise HTTPException(409, "Keep at least one active administrator.")
    member.role, member.active = body.role, body.active
    if not body.active:
        session.execute(
            delete(LoginSession).where(
                LoginSession.user_id == user_id, LoginSession.farm_id == FARM_ID
            )
        )
    session.commit()
