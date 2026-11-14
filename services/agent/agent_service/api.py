"""HTTP layer: parsing, auth, status codes, SSE framing, safe errors.

Handlers are thin: all agent logic lives in service.py.
"""

import hashlib
import hmac
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

import anyio
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import ValidationError
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from contracts.export import FieldShiftContracts
from contracts.models import SCHEMA_VERSION, AgentRequest, ErrorBody, ErrorCode, ErrorEnvelope

from .agent import Agent, build_agent
from .attestation import HEADER as ATTESTATION_HEADER
from .attestation import CLOCK_SKEW_S, MAX_LIFETIME_S, UNTRUSTED, AttestationError, RequestTrust
from .attestation import verify as verify_attestation
from .config import Settings
from .service import AgentService, ServiceError

log = logging.getLogger("agent_service.http")

REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
STATUS_BY_CODE: dict[str, int] = {
    "invalid_request": 400, "rate_limited": 429, "upstream_timeout": 504, "upstream_unavailable": 503,
    "llm_error": 502, "payload_too_large": 413, "internal": 500,
}


class PublicError(Exception):
    """An error whose code, key and detail are safe to show to any client."""

    def __init__(self, status: int, code: ErrorCode, message_key: str, *, retryable: bool = False,
                 detail: str | None = None, headers: dict[str, str] | None = None):
        super().__init__(message_key)
        self.status, self.headers = status, headers
        self.body = dict(code=code, message_key=message_key, retryable=retryable, detail=detail)


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _error_response(request: Request, status: int, code: ErrorCode, message_key: str, *,
                    retryable: bool = False, detail: str | None = None,
                    headers: dict[str, str] | None = None) -> JSONResponse:
    body = ErrorEnvelope(error=ErrorBody(code=code, message_key=message_key, retryable=retryable,
                                         request_id=_request_id(request), detail=detail))
    return JSONResponse(body.model_dump(mode="json", exclude_none=True), status_code=status, headers=headers)


def _summarize(errors: list[dict[str, Any]]) -> str:
    """Short, input-free summary of validation errors (ErrorBody.detail is limited to 300 chars)."""
    parts = [f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg', '')}" for e in errors[:4]]
    if len(errors) > 4:
        parts.append(f"(+{len(errors) - 4} more)")
    text = "; ".join(parts) or "invalid request"
    return text if len(text) <= 300 else text[:299] + "…"


# ------------------------------------------------------------------ middleware


class RequestContextMiddleware:
    """Assign a correlation id (X-Request-ID) and write one access-log line per request.

    Pure ASGI on purpose: it does not buffer or wrap streaming responses.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = Headers(scope=scope).get("x-request-id", "")
        request_id = incoming if REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started, status, response_started = time.perf_counter(), 500, False

        async def send_with_id(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status, response_started = message["status"], True
                MutableHeaders(scope=message).append("X-Request-ID", request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except BaseExceptionGroup as group:
            # FastAPI 0.162's SSE keep-alive task raises BrokenResourceError when the client
            # disconnects while a frame is still queued. The agent work is already cancelled by
            # then; only this exact shape (stream started, nothing but closed-stream errors) is
            # treated as a normal disconnect. Anything else propagates.
            _closed, rest = group.split((anyio.BrokenResourceError, anyio.ClosedResourceError))
            if not response_started or rest is not None:
                raise
            log.info("client disconnected mid-stream request_id=%s", request_id)
        finally:
            log.info("http method=%s path=%s status=%s duration_ms=%d request_id=%s",
                     scope["method"], scope["path"], status, (time.perf_counter() - started) * 1000, request_id)


# ------------------------------------------------------------------ dependencies


def require_api_key(request: Request) -> None:
    key = request.app.state.settings.api_key
    if key is None:  # allowed only outside production (enforced by Settings)
        return
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), key.get_secret_value().encode()):
        raise PublicError(401, "invalid_request", "error.unauthorized", headers={"WWW-Authenticate": "Bearer"})


def get_service(request: Request) -> AgentService:
    service = request.app.state.service
    if service is None:
        raise PublicError(503, "upstream_unavailable", "error.agent_not_ready", retryable=True)
    return service


async def agent_request_body(request: Request) -> AgentRequest:
    """Read and validate the body in JSON mode.

    FastAPI validates declared body models in Python mode, which the strict M0 models reject
    (dates, tuples and URLs arrive as JSON strings and arrays), so the body is parsed here.
    """
    settings: Settings = request.app.state.settings
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type != "application/json":
        # Also forces a CORS preflight for browsers: no "simple" cross-site POSTs.
        raise PublicError(415, "invalid_request", "error.unsupported_media_type", detail="Use application/json.")
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > settings.max_body_bytes:
        raise PublicError(413, "payload_too_large", "error.payload_too_large")
    body, size = bytearray(), 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > settings.max_body_bytes:
            raise PublicError(413, "payload_too_large", "error.payload_too_large")
        body += chunk
    try:
        parsed = AgentRequest.model_validate_json(bytes(body))
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False, include_input=False)) from None
    request.state.trust = _trust(request, settings, bytes(body), parsed)
    return parsed


def _trust(request: Request, settings: Settings, body: bytes, parsed: AgentRequest) -> RequestTrust:
    """Only a valid host signature over these exact bytes makes a request trusted. Headers that merely claim an
    identity (X-Forwarded-User, X-FieldShift-Principal, …) are never read."""
    token = request.headers.get(ATTESTATION_HEADER)
    key = settings.context_signing_key
    if token is None or key is None:
        if settings.require_context_attestation:
            raise PublicError(403, "invalid_request", "error.field_not_authorized",
                              detail="This service only accepts requests signed by the host backend.")
        return UNTRUSTED
    try:
        trust = verify_attestation(token, key.get_secret_value().encode(), body=body,
                                   field_id=parsed.field_context.field_id, revision=parsed.field_context.revision)
        if not _first_use(request.app.state.seen_attestations, token):
            raise AttestationError("replayed")
        return trust
    except AttestationError as e:
        log.warning("context attestation refused category=%s request_id=%s", e.category, _request_id(request))
        raise PublicError(403, "invalid_request", "error.field_not_authorized",
                          detail=f"Context attestation refused: {e.category}.") from None


_SEEN_TTL_S = MAX_LIFETIME_S + 2 * CLOCK_SKEW_S  # no token is still valid this long after it was first seen
_SEEN_MAX = 50_000


def _first_use(seen: dict[str, float], token: str) -> bool:
    """Signed requests are single-use (M6C): a replay, on any endpoint, would rerun a whole agent turn (model and
    service calls). The host backend signs every attempt, retries included.
    ponytail: per-process memory. Several workers or instances need a shared store or sticky routing; past
    _SEEN_MAX live entries the oldest is forgotten (only the host can mint that many valid tokens)."""
    now = time.monotonic()
    while seen and next(iter(seen.values())) < now - _SEEN_TTL_S:  # insertion order is time order
        seen.pop(next(iter(seen)))
    digest = hashlib.sha256(token.encode()).hexdigest()
    if digest in seen:
        return False
    if len(seen) >= _SEEN_MAX:
        seen.pop(next(iter(seen)))
    seen[digest] = now
    return True


Service = Annotated[AgentService, Depends(get_service)]
Body = Annotated[AgentRequest, Depends(agent_request_body)]


# ------------------------------------------------------------------ OpenAPI


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


def _error_doc(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/json": {"schema": _ref("ErrorEnvelope")}}}


AGENT_DOCS: dict[str, Any] = {
    "openapi_extra": {"requestBody": {"required": True, "content": {"application/json": {"schema": _ref("AgentRequest")}}}},
    "dependencies": [Depends(require_api_key)],
}
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: _error_doc("Missing or wrong bearer token (only when AGENT_API_KEY is set)."),
    413: _error_doc("Body larger than AGENT_MAX_BODY_BYTES."),
    415: _error_doc("Content-Type is not application/json."),
    422: _error_doc("Body does not match the AgentRequest contract."),
    503: _error_doc("The agent is not ready."),
}


def _openapi(app: FastAPI) -> Callable[[], dict[str, Any]]:
    def build() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
            # Publish the M0 contract types (validation mode) so request/response refs resolve.
            defs = FieldShiftContracts.model_json_schema(ref_template="#/components/schemas/{model}")["$defs"]
            components = schema.setdefault("components", {}).setdefault("schemas", {})
            for name, definition in defs.items():
                components.setdefault(name, definition)
            app.openapi_schema = schema
        return app.openapi_schema

    return build


# ------------------------------------------------------------------ app


def create_app(settings: Settings | None = None,
               agent_factory: Callable[[Settings], Agent] = build_agent) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.service, app.state.agent_name, app.state.not_ready_reason = None, None, "agent_init_failed"
        try:
            agent = agent_factory(settings)  # built once; shared by all requests
            app.state.service = AgentService(agent, settings.turn_timeout_s, settings.log_message_text)
            app.state.agent_name = agent.name
            log.info("agent ready name=%s environment=%s auth=%s", agent.name, settings.environment,
                     "on" if settings.api_key else "off")
        except Exception as e:
            # e.g. Gemini without credentials: reported as such, never replaced by another (paid or scripted) model
            app.state.not_ready_reason = getattr(e, "reason", "agent_init_failed")
            log.exception("agent initialisation failed: service is live but not ready")
        yield
        service = app.state.service
        if service is not None and hasattr(service.agent, "aclose"):
            await service.agent.aclose()  # close provider HTTP clients

    production = settings.environment == "production"
    app = FastAPI(
        title="Field Shift agent service",
        version=f"0.1.0 (contracts {SCHEMA_VERSION})",
        description="M1 foundation. The only agent is a deterministic development agent (no LLM).",
        lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None,
        openapi_url=None if production else "/openapi.json",
    )
    app.state.settings = settings
    app.state.seen_attestations = {}  # digest -> first seen (monotonic); see _first_use
    app.openapi = _openapi(app)  # type: ignore[method-assign]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # auth is a bearer header, never cookies
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)  # outermost: ids on every response, CORS ones too

    @app.exception_handler(PublicError)
    async def _public(request: Request, exc: PublicError) -> JSONResponse:
        return _error_response(request, exc.status, headers=exc.headers, **exc.body)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(request, 422, "invalid_request", "error.invalid_request", detail=_summarize(list(exc.errors())))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        key = {404: "error.not_found", 405: "error.method_not_allowed"}.get(exc.status_code, "error.invalid_request")
        code: ErrorCode = "internal" if exc.status_code >= 500 else "invalid_request"
        return _error_response(request, exc.status_code, code, key, headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error request_id=%s", _request_id(request))
        return _error_response(request, 500, "internal", "error.internal")

    @app.get("/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        """Process is up. Never touches the agent or external services."""
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"], responses={503: {"description": "Agent failed to initialise."}})
    async def ready(request: Request) -> JSONResponse:
        """The configured agent initialised and can accept requests."""
        if request.app.state.service is None:
            return JSONResponse({"status": "not_ready", "reason": request.app.state.not_ready_reason}, status_code=503)
        body: dict[str, Any] = {"status": "ready", "agent": request.app.state.agent_name}
        provider = getattr(request.app.state.service.agent, "provider", None)
        if provider is not None and provider.name != "scripted-llm":
            body["model"] = provider.model_id  # the real model configured to serve requests (not shown for the script)
        knowledge = getattr(request.app.state.service.agent, "knowledge", None)
        if hasattr(request.app.state.service.agent, "knowledge"):
            body["knowledge"] = knowledge.status() if knowledge is not None else {"mode": "unavailable"}
        # External services are reported separately: a ready agent does not mean geo or simulation is reachable,
        # and a reachable stub or fixture is reported as such (service_mode), never as a live integration.
        integrations = getattr(request.app.state.service.agent, "integrations", None)
        if integrations is not None:
            body["integrations"] = await integrations.status()
        return JSONResponse(body)

    @app.post(
        "/v1/agent/message", tags=["agent"], response_class=Response,
        responses={200: {"description": "The final AgentMessage of the turn.",
                         "content": {"application/json": {"schema": _ref("AgentMessage")}}},
                   504: _error_doc("The turn exceeded AGENT_TURN_TIMEOUT_S."),
                   500: _error_doc("The agent failed or broke the contract."), **ERROR_RESPONSES},
        **AGENT_DOCS,
    )
    async def message(request: Request, service: Service, body: Body) -> Response:
        """Run one turn and return the final `AgentMessage` as JSON."""
        try:
            result = await service.respond(body, getattr(request.state, "trust", UNTRUSTED))
        except ServiceError as exc:
            e = exc.error
            return _error_response(request, STATUS_BY_CODE[e.code], e.code, e.message_key, retryable=e.retryable)
        return Response(result.model_dump_json(), media_type="application/json")

    @app.post(
        "/v1/agent/stream", tags=["agent"], response_class=EventSourceResponse,
        responses={200: {"description": "SSE stream. Each frame: `event: <AgentEvent.event>` and "
                                        "`data: <AgentEvent JSON>`. See README › Streaming protocol.",
                         "content": {"text/event-stream": {"schema": _ref("AgentEvent")}}},
                   **ERROR_RESPONSES},
        **AGENT_DOCS,
    )
    async def stream(request: Request, service: Service, body: Body) -> AsyncIterator[ServerSentEvent]:
        """Run one turn and stream `AgentEvent`s as Server-Sent Events."""
        seq = 0
        async for event in service.stream(body, getattr(request.state, "trust", UNTRUSTED)):
            seq += 1
            yield ServerSentEvent(event=event.event, id=str(seq), raw_data=event.model_dump_json())

    return app
