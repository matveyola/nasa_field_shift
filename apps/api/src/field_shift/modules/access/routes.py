from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select

from field_shift.db import SessionDep
from field_shift.modules.access import service
from field_shift.modules.access.dependencies import require_admin, require_principal
from field_shift.modules.access.middleware import COOKIE_NAME
from field_shift.modules.access.models import Invitation, LoginSession, Membership, User
from field_shift.modules.access.passwords import digest_token, password_hash, verify_password
from field_shift.modules.access.schemas import (
    Credentials,
    InviteCreate,
    MemberUpdate,
    PasswordUpdate,
    Registration,
)
from field_shift.modules.farm.models import FARM_ID

router = APIRouter(tags=["access"])


def team_mode(request: Request) -> None:
    if request.app.state.settings.access_mode != "team":
        raise HTTPException(409, "Team access is not enabled on this deployment.")


def session_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=service.SESSION_SECONDS,
        path="/api",
        secure=request.app.state.settings.access_secure_cookie,
        httponly=True,
        samesite="strict",
    )


def auth_throttle(request: Request, session: SessionDep, identity: str) -> None:
    address = request.client.host if request.client else "unknown"
    # Ignore forwarding headers. A trusted reverse proxy may set the ASGI client address.
    service.throttle(session, f"auth-ip:{address}", limit=40)
    service.throttle(session, f"auth-account:{identity}", limit=10)


@router.get("/auth/session")
def session_info(request: Request, session: SessionDep):
    settings = request.app.state.settings
    principal = getattr(request.state, "principal", None)
    configured = service.has_admin(session) if settings.access_mode == "team" else False
    return {
        "mode": settings.access_mode,
        "authenticated": principal is not None,
        "needs_setup": settings.access_mode == "team" and not configured,
        "setup_available": bool(settings.access_bootstrap_token),
        "user": {"id": principal.user_id, "name": principal.name, "email": principal.email}
        if principal
        else None,
        "role": principal.role if principal else None,
        "csrf_token": principal.csrf if principal else None,
    }


@router.post("/auth/setup", dependencies=[Depends(team_mode)])
def setup(body: Registration, request: Request, response: Response, session: SessionDep):
    auth_throttle(request, session, "setup")
    session_cookie(request, response, service.setup(session, body, request.app.state.settings))
    return {"ok": True}


@router.post("/auth/login", dependencies=[Depends(team_mode)])
def login(body: Credentials, request: Request, response: Response, session: SessionDep):
    auth_throttle(request, session, body.email)
    session_cookie(request, response, service.login(session, body))
    return {"ok": True}


@router.post("/auth/accept", dependencies=[Depends(team_mode)])
def accept(body: Registration, request: Request, response: Response, session: SessionDep):
    auth_throttle(request, session, body.email)
    session_cookie(request, response, service.accept(session, body))
    return {"ok": True}


@router.post("/auth/logout", dependencies=[Depends(team_mode), Depends(require_principal)])
def logout(request: Request, response: Response, session: SessionDep):
    session.execute(
        delete(LoginSession).where(
            LoginSession.token_hash == digest_token(request.cookies.get(COOKIE_NAME, ""))
        )
    )
    session.commit()
    response.delete_cookie(
        COOKIE_NAME,
        path="/api",
        secure=request.app.state.settings.access_secure_cookie,
        httponly=True,
        samesite="strict",
    )
    return {"ok": True}


@router.post("/auth/password", dependencies=[Depends(team_mode)])
def change_password(
    body: PasswordUpdate, request: Request, response: Response, session: SessionDep
):
    principal = require_principal(request)
    auth_throttle(request, session, f"password:{principal.user_id}")
    service.lock_farm(session)
    user = session.get(User, principal.user_id, with_for_update=True, populate_existing=True)
    if user is None or not verify_password(body.current_password, user.password_hash):
        raise HTTPException(403, "The current password is incorrect.")
    user.password_hash = password_hash(body.new_password)
    session.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    session_cookie(request, response, service.issue_session(session, user))
    return {"ok": True}


@router.get("/team", dependencies=[Depends(team_mode), Depends(require_principal)])
def team(session: SessionDep, request: Request):
    principal = require_principal(request)
    members = session.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.farm_id == FARM_ID)
        .order_by(User.name, User.id)
    )
    invitations = (
        list(
            session.scalars(
                select(Invitation).where(
                    Invitation.farm_id == FARM_ID,
                    Invitation.consumed_at.is_(None),
                    Invitation.expires_at > service.now(),
                )
            )
        )
        if principal.role == "admin"
        else []
    )
    return {
        "members": [
            {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "role": member.role,
                "active": member.active,
            }
            for user, member in members
        ],
        "invitations": [
            {
                "id": invite.id,
                "email": invite.email,
                "role": invite.role,
                "expires_at": invite.expires_at,
            }
            for invite in invitations
        ],
    }


@router.post(
    "/team/invitations", dependencies=[Depends(team_mode), Depends(require_admin)], status_code=201
)
def create_invitation(body: InviteCreate, session: SessionDep, request: Request):
    return service.invite(session, body, require_admin(request).user_id)


@router.delete(
    "/team/invitations/{invitation_id}", dependencies=[Depends(team_mode), Depends(require_admin)]
)
def revoke_invitation(invitation_id: str, session: SessionDep, request: Request):
    service.lock_admin(session, require_admin(request).user_id)
    invite = session.scalar(
        select(Invitation).where(Invitation.id == invitation_id, Invitation.farm_id == FARM_ID)
    )
    if invite is None:
        raise HTTPException(404, "Invitation was not found.")
    invite.consumed_at = service.now()
    session.commit()
    return {"ok": True}


@router.patch("/team/members/{user_id}", dependencies=[Depends(team_mode), Depends(require_admin)])
def patch_member(user_id: int, body: MemberUpdate, session: SessionDep, request: Request):
    service.update_member(session, user_id, body, require_admin(request).user_id)
    return {"ok": True}
