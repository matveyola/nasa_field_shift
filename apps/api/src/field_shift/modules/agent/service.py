from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from field_shift.errors import ConflictError, NotFoundError
from field_shift.jobs.models import Job, JobKind, JobStatus
from field_shift.modules.agent.constraints import agent_constraints
from field_shift.modules.agent.field_context import HISTORICAL_CHART, build_field_context
from field_shift.modules.agent.models import Conversation, Message
from field_shift.modules.agent.provider import AgentContext, AgentProvider
from field_shift.modules.agent.schemas import (
    AgentReply,
    AgentReplyMetadata,
    AgentRequest,
    ConversationDetail,
    MessageRead,
)
from field_shift.modules.agent.signing import LOCAL_PRINCIPAL
from field_shift.modules.farm.service import recent_scouting
from field_shift.modules.fields.schemas import FieldRead
from field_shift.modules.fields.service import require_field
from field_shift.modules.geo_ml.service import latest_surface_moisture
from field_shift.modules.imagery.vegetation import vegetation_footprint, vegetation_season
from field_shift.modules.simulation.forecast import latest_water_forecast
from field_shift.modules.simulation.schemas import WaterResult, WaterSource
from field_shift.modules.terrain.service import latest_terrain
from field_shift.modules.weather.scenarios import Scenario


def _get_conversation(
    session: Session, conversation_id: UUID, *, lock: bool = False
) -> Conversation:
    query = select(Conversation).where(Conversation.id == conversation_id)
    conversation = session.scalar(query.with_for_update() if lock else query)
    if conversation is None:
        raise NotFoundError("Conversation was not found")
    require_field(session, conversation.field_id)
    return conversation


def _recent_messages(session: Session, conversation_id: UUID, limit: int) -> list[MessageRead]:
    messages = session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(limit)
    )
    return [MessageRead.model_validate(message) for message in reversed(list(messages))]


def list_conversations(session: Session, field_id: str) -> list[Conversation]:
    require_field(session, field_id)
    return list(
        session.scalars(
            select(Conversation)
            .where(Conversation.field_id == field_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id)
            .limit(50)
        )
    )


def conversation_detail(session: Session, conversation_id: UUID) -> ConversationDetail:
    conversation = _get_conversation(session, conversation_id)
    return ConversationDetail(
        id=conversation.id,
        field_id=conversation.field_id,
        season=conversation.season,
        scenario=conversation.scenario,
        title=conversation.title,
        updated_at=conversation.updated_at,
        messages=_recent_messages(session, conversation.id, 100),
    )


def _saved_water_run(
    session: Session,
    run_id: UUID,
    field_id: str,
    season: int,
    scenario: Scenario,
    boundary_revision: int,
) -> WaterResult:
    """The completed water-balance run the farmer selected, for exactly this field context.
    The caller has already checked the field belongs to the caller's farm (require_field)."""
    job = session.get(Job, str(run_id))
    if (
        job is None
        or job.kind != JobKind.SIMULATION
        or job.analysis != "water_balance"
        or job.field_id != field_id
    ):
        raise NotFoundError("The simulation run was not found for this field")
    if job.status != JobStatus.SUCCEEDED or job.result is None:
        raise ConflictError("The simulation run has no completed result")
    if job.boundary_revision != boundary_revision:
        raise ConflictError("The field boundary changed. Run a new simulation.")

    try:
        result = WaterResult.model_validate(job.result)
    except ValidationError:
        raise ConflictError("The simulation run has no completed result") from None
    if (result.field_id, result.season, result.scenario) != (field_id, season, scenario):
        raise ConflictError("The simulation result does not match the requested field context")
    return result


def _stored_water_result(
    session: Session, payload: AgentRequest, boundary_revision: int
) -> WaterResult | None:
    if payload.run_id is None:
        return None
    return _saved_water_run(
        session,
        payload.run_id,
        payload.field_id,
        payload.season,
        payload.scenario,
        boundary_revision,
    )


def assistant_field_context(
    session: Session,
    field_id: str,
    season: int,
    scenario: Scenario,
    locale: Literal["uk", "en"],
    run_id: UUID | None = None,
) -> dict:
    """The «Польовий помічник» widget's field context: the same saved evidence as chat,
    built here, never taken from the browser. The agent's own tools may fetch weather.

    With `run_id` (the farmer explicitly selected a saved run) the context explains that
    historical run instead of the current forecast; without it no saved run is ever attached."""
    now = datetime.now(UTC)
    field = require_field(session, field_id)
    field_read = FieldRead.model_validate(field)
    water = None
    if run_id is not None:
        water = _saved_water_run(
            session, run_id, field.id, season, scenario, field.boundary_revision
        )
        if water.demo or water.source != WaterSource.HISTORICAL or water.provenance is None:
            raise ConflictError("The selected run is a demo, not a historical simulation")
    # A selected historical run is never shown next to (or as) the current forecast or NDVI.
    current = run_id is None
    forecast = latest_water_forecast(session, field.id) if current and season == now.year else None
    vegetation = (
        vegetation_season(session, field.id, season, now=now, include_cells=False)
        if current and 2017 <= season <= now.year
        else None
    )
    # Stored reads only: a missing ERA5-Land or terrain result stays missing, nothing is computed.
    surface_moisture = latest_surface_moisture(session, field.id, season, "historical")
    terrain = latest_terrain(session, field.id)
    scouting = recent_scouting(session, field.id, season, now.date())
    # The farmer's own decisions, from the host's record: never the browser's context or history.
    decided = agent_constraints(session, field.id, season, field.boundary_revision)
    session.rollback()  # the agent turn never holds a DB connection or row lock
    context = build_field_context(
        field_read,
        season=season,
        locale=locale,
        now=now,
        forecast=forecast,
        vegetation=vegetation,
        water=water,
        surface_moisture=surface_moisture,
        terrain=terrain,
        scenario=scenario,
        scouting=scouting,
        constraints=decided,
    )
    if water is not None and not any(
        chart["chart_id"] == HISTORICAL_CHART for chart in context["available_charts"]
    ):
        # The saved days or provenance failed the evidence checks: say so, never fall back.
        raise ConflictError("The selected run cannot be used as evidence. Run a new simulation.")
    return context


def _check_context(conversation: Conversation, payload: AgentRequest) -> None:
    if (conversation.field_id, conversation.season, conversation.scenario) != (
        payload.field_id,
        payload.season,
        payload.scenario,
    ):
        raise ConflictError("Start a new conversation for a different field context")


def chat(
    session: Session,
    payload: AgentRequest,
    provider: AgentProvider,
    principal: str = LOCAL_PRINCIPAL,
) -> AgentReply:
    now = datetime.now(UTC)
    conversation_id = payload.conversation_id or uuid4()
    field = require_field(session, payload.field_id)
    field_read = FieldRead.model_validate(field)
    boundary_revision = field.boundary_revision
    stored_result = _stored_water_result(session, payload, boundary_revision)
    # Read durable current-boundary evidence. Chat never fetches imagery/weather or runs models.
    forecast = (
        latest_water_forecast(session, field.id)
        if (payload.run_id is None and payload.season == now.year)
        else None
    )
    vegetation = (
        vegetation_season(session, field.id, payload.season, now=now, include_cells=False)
        if (payload.run_id is None and 2017 <= payload.season <= now.year)
        else None
    )
    scouting = recent_scouting(session, field.id, payload.season, now.date())
    history: list[MessageRead] = []
    if payload.conversation_id is not None:
        conversation = _get_conversation(session, payload.conversation_id)
        _check_context(conversation, payload)
        history = _recent_messages(session, conversation.id, 20)

    # Only detached inputs reach the provider. Its work never holds a DB connection or row lock.
    session.rollback()
    answer = provider.reply(
        payload.message,
        AgentContext(
            stored_result,
            payload.run_id,
            tuple(history),
            field_id=payload.field_id,
            season=payload.season,
            forecast=forecast,
            vegetation=vegetation,
            today=now.date(),
            field=field_read,
            captured_at=now,
            scenario=payload.scenario,
            locale=payload.locale,
            conversation_id=conversation_id,
            scouting=scouting,
            principal=principal,
        ),
    )
    now = datetime.now(UTC)
    current_field = require_field(session, payload.field_id)
    # Field/planting edits lock this row too. Keep the post-provider check valid
    # until the evidence-bearing message is committed, without locking during inference.
    session.refresh(current_field, with_for_update=True)
    if current_field.boundary_revision != boundary_revision:
        raise ConflictError("The field boundary changed. Ask again for the updated field.")
    if vegetation is not None:
        _, fingerprint = vegetation_footprint(session, current_field, payload.season)
        if fingerprint != vegetation.geometry_fingerprint:
            raise ConflictError("The planted area changed. Ask again for the updated field.")

    if payload.conversation_id is None:
        conversation = Conversation(
            id=conversation_id,
            field_id=payload.field_id,
            season=payload.season,
            scenario=payload.scenario,
            title=payload.message[:120],
            updated_at=now,
        )
        session.add(conversation)
        session.flush()
    else:
        conversation = _get_conversation(session, payload.conversation_id, lock=True)
        _check_context(conversation, payload)
        latest_id = session.scalar(
            select(Message.id)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.id.desc())
            .limit(1)
        )
        if latest_id != (history[-1].id if history else None):
            raise ConflictError("The conversation changed. Reload it before sending another reply")
        conversation.updated_at = now
    metadata = AgentReplyMetadata(
        provider=answer.provider,
        citations=list(answer.citations),
        options=list(answer.options),
    )
    session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                role="user",
                text=payload.message,
                demo=False,
                actions=[],
            ),
            Message(
                conversation_id=conversation.id,
                role="assistant",
                text=answer.message,
                demo=answer.demo,
                actions=[action.model_dump(mode="json") for action in answer.actions],
                reply_metadata=metadata.model_dump(mode="json"),
            ),
        ]
    )
    session.commit()
    return AgentReply(
        message=answer.message,
        demo=answer.demo,
        actions=list(answer.actions),
        conversation_id=conversation.id,
        **metadata.model_dump(),
    )
