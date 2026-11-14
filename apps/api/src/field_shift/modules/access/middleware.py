from secrets import compare_digest
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from field_shift.modules.access import service
from field_shift.modules.farm.models import FARM_ID

COOKIE_NAME = "fs_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC = {
    "/api/health",
    "/api/auth/session",
    "/api/auth/login",
    "/api/auth/setup",
    "/api/auth/accept",
}
SELF_SERVICE = {"/api/auth/logout", "/api/auth/password"}


def authorize(request: Request) -> None:
    settings = request.app.state.settings
    path = request.url.path.rstrip("/")
    unsafe = request.method not in SAFE_METHODS
    allowed_host = urlsplit(settings.access_public_origin).hostname
    allowed_hosts = (
        {allowed_host} if allowed_host else {"localhost", "127.0.0.1", "::1", "testserver"}
    )
    if request.url.hostname not in allowed_hosts and path != "/api/health":
        raise HTTPException(400, "The request host is not configured for this deployment.")
    if unsafe:
        expected_origin = settings.access_public_origin or str(request.base_url).rstrip("/")
        origin = request.headers.get("origin")
        if origin and origin != expected_origin:
            raise HTTPException(403, "The request origin is not allowed.")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Cross-site requests are not allowed.")
    if settings.access_mode == "local":
        request.state.principal = service.Principal(None, FARM_ID, "Local workspace", None, "admin")
        return
    # A custom request header makes the unauthenticated JSON actions non-simple CORS requests.
    if unsafe and request.headers.get("x-field-shift-request") != "1":
        raise HTTPException(403, "A same-origin application request is required.")
    with request.app.state.session_factory() as session:
        principal = service.current_principal(session, request.cookies.get(COOKIE_NAME))
        request.state.principal = principal
        if path in PUBLIC:
            return
        if principal is None:
            raise HTTPException(401, "Sign in to continue.")
        if unsafe:
            if not compare_digest(request.headers.get("x-csrf-token", ""), principal.csrf or ""):
                raise HTTPException(
                    403, "Your session token changed. Reload the page and try again."
                )
            if principal.role == "viewer" and path not in SELF_SERVICE:
                raise HTTPException(
                    403, "Your role allows viewing only. Ask an administrator for edit access."
                )
        if unsafe and (
            path == "/api/agent/chat"
            or (path.startswith("/api/agent/assistant/") and path.endswith("/v1/agent/stream"))
        ):
            service.throttle(session, f"agent:{principal.user_id}", limit=15, seconds=60)


async def access_middleware(request: Request, call_next):
    if request.url.path.startswith("/api/") or request.url.path in {
        "/docs",
        "/redoc",
        "/openapi.json",
    }:
        try:
            await run_in_threadpool(authorize, request)
        except HTTPException as error:
            return JSONResponse(
                status_code=error.status_code,
                content={"detail": error.detail},
                headers={**(error.headers or {}), "Cache-Control": "no-store"},
            )
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        if "no-store" not in response.headers.get("Cache-Control", ""):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response
