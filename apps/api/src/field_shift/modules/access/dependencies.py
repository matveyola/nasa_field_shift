from fastapi import HTTPException, Request

from field_shift.modules.access.service import Principal


def require_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(401, "Sign in to continue.")
    return principal


def require_admin(request: Request) -> Principal:
    principal = require_principal(request)
    if principal.role != "admin":
        raise HTTPException(403, "Administrator access is required.")
    return principal
