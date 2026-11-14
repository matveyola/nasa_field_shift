from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse, StreamingResponse

from field_shift.db import SessionDep
from field_shift.modules.agent import service
from field_shift.modules.agent.remote import RemoteAgentError, RemoteAgentProvider
from field_shift.modules.agent.schemas import (
    AgentReply,
    AgentRequest,
    AgentStatus,
    ConversationDetail,
    ConversationRead,
)
from field_shift.modules.fields.schemas import Season
from field_shift.modules.weather.scenarios import Scenario

router = APIRouter(prefix="/agent", tags=["agent"])


@router.get("/status", response_model=AgentStatus)
def status(request: Request):
    provider = request.app.state.agent_provider
    if isinstance(provider, RemoteAgentProvider):
        return provider.status()
    return AgentStatus(provider="template", demo=True, available=True)


@router.post("/chat", response_model=AgentReply)
def chat(payload: AgentRequest, request: Request, session: SessionDep):
    return service.chat(session, payload, request.app.state.agent_provider)


# «Польовий помічник»: the widget's base URL is this prefix; it calls {base}/health/ready and
# {base}/v1/agent/stream like the agent service itself, and the API stays its backend-for-frontend.
ASSISTANT = "/assistant/{field_id}/{season}/{scenario}"
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


@router.get(ASSISTANT + "/health/ready")
def assistant_health(field_id: str, season: Season, scenario: Scenario, request: Request):
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
    provider = _remote(request)
    locale = payload.get("locale") if payload.get("locale") in ("uk", "en") else "uk"
    context = service.assistant_field_context(session, field_id, season, scenario, locale)
    return StreamingResponse(
        provider.stream(payload, context),
        media_type="text/event-stream",
        headers={**NO_STORE, "X-Accel-Buffering": "no"},  # nginx passes frames as they come
    )


@router.get("/conversations", response_model=list[ConversationRead])
def conversations(field_id: str, session: SessionDep):
    return service.list_conversations(session, field_id)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def conversation(conversation_id: UUID, session: SessionDep):
    return service.conversation_detail(session, conversation_id)
