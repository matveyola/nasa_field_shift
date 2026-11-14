from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from field_shift_science.fields.schemas import FieldRead
from field_shift_science.simulation.schemas import WaterResult
from field_shift_science.weather.scenarios import Scenario
from sqlalchemy import select
from sqlalchemy.orm import Session

from field_shift.errors import ConflictError, NotFoundError
from field_shift.jobs.models import Job, JobKind, JobStatus
from field_shift.modules.agent.field_context import build_field_context
from field_shift.modules.agent.models import Conversation, Message
from field_shift.modules.agent.provider import AgentContext, AgentProvider
from field_shift.modules.agent.schemas import (
    AgentReply,
    AgentReplyMetadata,
    AgentRequest,
    ConversationDetail,
    MessageRead,
)
from field_shift.modules.farm.service import recent_scouting
from field_shift.modules.fields.service import require_field
from field_shift.modules.imagery.vegetation import vegetation_footprint, vegetation_season
from field_shift.modules.simulation.forecast import latest_water_forecast


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


def _stored_water_result(
    session: Session, payload: AgentRequest, boundary_revision: int
) -> WaterResult | None:
    if payload.run_id is None:
        return None

    job = session.get(Job, str(payload.run_id))
    if (
        job is None
        or job.kind != JobKind.SIMULATION
        or job.analysis != "water_balance"
        or job.field_id != payload.field_id
    ):
        raise NotFoundError("The simulation run was not found for this field")
    if job.status != JobStatus.SUCCEEDED or job.result is None:
        raise ConflictError("The simulation run has no completed result")
    if job.boundary_revision != boundary_revision:
        raise ConflictError("The field boundary changed. Run a new simulation.")

    result = WaterResult.model_validate(job.result)
    if (result.field_id, result.season, result.scenario) != (
        payload.field_id,
        payload.season,
        payload.scenario,
    ):
        raise ConflictError("The simulation result does not match the requested field context")
    return result


def assistant_field_context(
    session: Session, field_id: str, season: int, scenario: Scenario, locale: Literal["uk", "en"]
) -> dict:
    """The «Польовий помічник» widget's field context: the same saved evidence as chat,
    built here, never taken from the browser. The agent's own tools may fetch weather."""
    now = datetime.now(UTC)
    field = require_field(session, field_id)
    field_read = FieldRead.model_validate(field)
    forecast = latest_water_forecast(session, field.id) if season == now.year else None
    vegetation = (
        vegetation_season(session, field.id, season, now=now, include_cells=False)
        if 2017 <= season <= now.year
        else None
    )
    scouting = recent_scouting(session, field.id, season, now.date())
    session.rollback()  # the agent turn never holds a DB connection or row lock
    return build_field_context(
        field_read,
        season=season,
        locale=locale,
        now=now,
        forecast=forecast,
        vegetation=vegetation,
        scenario=scenario,
        scouting=scouting,
    )


def _check_context(conversation: Conversation, payload: AgentRequest) -> None:
    if (conversation.field_id, conversation.season, conversation.scenario) != (
        payload.field_id,
        payload.season,
        payload.scenario,
    ):
        raise ConflictError("Start a new conversation for a different field context")


def chat(session: Session, payload: AgentRequest, provider: AgentProvider) -> AgentReply:
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
