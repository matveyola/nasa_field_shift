from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from field_shift.db import SessionDep
from field_shift.modules.agent import constraints, service
from field_shift.modules.agent.remote import RemoteAgentError, RemoteAgentProvider
from field_shift.modules.agent.schemas import (
    AgentReply,
    AgentRequest,
    AgentStatus,
    ConversationDetail,
    ConversationRead,
)
from field_shift.modules.agent.signing import LOCAL_PRINCIPAL, agent_principal
from field_shift.modules.fields.schemas import Season
from field_shift.modules.fields.service import require_field
from field_shift.modules.weather.scenarios import Scenario

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get("/status", response_model=AgentStatus)
def status(request: Request):
    provider = request.app.state.agent_provider
    if isinstance(provider, RemoteAgentProvider):
        return provider.status()
    return AgentStatus(provider="template", demo=True, available=True)


def _principal(request: Request) -> str:
    """The caller the API attests to the agent, from the access middleware's server-side session
    (team mode: the signed-in member; local mode: the shared workspace). Never from the browser."""
    principal = getattr(request.state, "principal", None)
    if principal is None:
        return LOCAL_PRINCIPAL
    return agent_principal(principal.user_id, principal.farm_id)


@router.post("/chat", response_model=AgentReply)
def chat(payload: AgentRequest, request: Request, session: SessionDep):
    return service.chat(session, payload, request.app.state.agent_provider, _principal(request))


# «Польовий помічник»: the widget's base URL is this prefix; it calls {base}/health/ready and
# {base}/v1/agent/stream like the agent service itself, and the API stays its backend-for-frontend.
ASSISTANT = "/assistant/{field_id}/{season}/{scenario}"
# The same three endpoints for one saved water-balance run the farmer explicitly selected. A path
# segment, not a query string: the widget appends its endpoint paths to its base URL.
ASSISTANT_RUN = ASSISTANT + "/runs/{run_id}"
NO_STORE = {"Cache-Control": "no-store"}


def _remote(request: Request) -> RemoteAgentProvider:
    provider = request.app.state.agent_provider
    if not isinstance(provider, RemoteAgentProvider):
        raise RemoteAgentError(503, "The assistant is not configured.")
    return provider


@router.get(ASSISTANT + "/field-context")
def assistant_field_context(
    field_id: str,
    season: Season,
    scenario: Scenario,
    session: SessionDep,
    locale: Literal["uk", "en"] = "uk",
):
    return JSONResponse(
        service.assistant_field_context(session, field_id, season, scenario, locale),
        headers=NO_STORE,
    )


@router.get(ASSISTANT_RUN + "/field-context")
def assistant_run_field_context(
    field_id: str,
    season: Season,
    scenario: Scenario,
    run_id: UUID,
    session: SessionDep,
    locale: Literal["uk", "en"] = "uk",
):
    return JSONResponse(
        service.assistant_field_context(session, field_id, season, scenario, locale, run_id),
        headers=NO_STORE,
    )


@router.get(ASSISTANT + "/health/ready")
def assistant_health(field_id: str, season: Season, scenario: Scenario, request: Request):
    return _health(request)


@router.get(ASSISTANT_RUN + "/health/ready")
def assistant_run_health(
    field_id: str, season: Season, scenario: Scenario, run_id: UUID, request: Request
):
    return _health(request)


def _health(request: Request) -> JSONResponse:
    provider = request.app.state.agent_provider
    if not isinstance(provider, RemoteAgentProvider):
        return JSONResponse({"status": "not_configured"}, status_code=503, headers=NO_STORE)
    code, body = provider.assistant_health()
    return JSONResponse(body, status_code=code, headers=NO_STORE)


@router.post(ASSISTANT + "/v1/agent/stream")
def assistant_stream(
    field_id: str,
    season: Season,
    scenario: Scenario,
    request: Request,
    session: SessionDep,
    payload: dict = Body(...),
):
    return _stream(request, session, payload, field_id, season, scenario, None)


@router.post(ASSISTANT_RUN + "/v1/agent/stream")
def assistant_run_stream(
    field_id: str,
    season: Season,
    scenario: Scenario,
    run_id: UUID,
    request: Request,
    session: SessionDep,
    payload: dict = Body(...),
):
    return _stream(request, session, payload, field_id, season, scenario, run_id)


def _stream(
    request: Request,
    session: Session,
    payload: dict,
    field_id: str,
    season: int,
    scenario: Scenario,
    run_id: UUID | None,
) -> StreamingResponse:
    provider = _remote(request)
    locale = payload.get("locale") if payload.get("locale") in ("uk", "en") else "uk"
    # The server builds (and signs) the context from its own records; the browser's is dropped.
    context = service.assistant_field_context(session, field_id, season, scenario, locale, run_id)
    return StreamingResponse(
        provider.stream(payload, context, _principal(request)),
        media_type="text/event-stream",
        headers={**NO_STORE, "X-Accel-Buffering": "no"},  # nginx passes frames as they come
    )


# Farmer-confirmed planning constraints, per field and planning season (see constraints.py).
CONSTRAINTS = "/constraints/{field_id}/{season}"


@router.get(CONSTRAINTS)
def planning_constraints(field_id: str, season: Season, session: SessionDep):
    field = require_field(session, field_id)
    return {
        "constraints": constraints.planning_constraints(
            session, field.id, season, field.boundary_revision
        )
    }


@router.post(CONSTRAINTS)
def decide_constraints(
    field_id: str,
    season: Season,
    decision: constraints.ConstraintDecision,
    request: Request,
    session: SessionDep,
):
    principal = getattr(request.state, "principal", None)
    decided_by = principal.user_id if principal is not None else None
    try:
        current, replaced = constraints.decide(session, field_id, season, decision, decided_by)
    except constraints.ReplacementRequired as required:
        # Nothing was saved: the farmer reviews each change and confirms it explicitly.
        return JSONResponse(
            {
                "detail": str(required),
                "code": "replacement_needs_confirmation",
                "replacements": required.replacements,
            },
            status_code=409,
        )
    return {"constraints": current, "replaced": replaced}


@router.delete(CONSTRAINTS + "/{kind}")
def withdraw_constraint(
    field_id: str,
    season: Season,
    kind: constraints.ConstraintType,
    boundary_revision: Annotated[int, Query(ge=0)],
    token: Annotated[constraints.Token, Query()],
    session: SessionDep,
):
    return {
        "constraints": constraints.withdraw(
            session, field_id, season, kind, boundary_revision, token
        )
    }


@router.get("/conversations", response_model=list[ConversationRead])
def conversations(field_id: str, session: SessionDep):
    return service.list_conversations(session, field_id)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def conversation(conversation_id: UUID, session: SessionDep):
    return service.conversation_detail(session, conversation_id)
