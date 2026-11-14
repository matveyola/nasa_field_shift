from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from field_shift.db import Base


class Conversation(Base):
    __tablename__ = "agent_conversations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    field_id: Mapped[str] = mapped_column(ForeignKey("fields.id"), index=True)
    season: Mapped[int]
    scenario: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(120))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Message(Base):
    __tablename__ = "agent_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(12))
    text: Mapped[str] = mapped_column(Text)
    demo: Mapped[bool]
    actions: Mapped[list[dict]] = mapped_column(JSON)
    reply_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    @property
    def provider(self):
        return (self.reply_metadata or {}).get("provider")

    @property
    def citations(self):
        return (self.reply_metadata or {}).get("citations", [])

    @property
    def options(self):
        return (self.reply_metadata or {}).get("options", [])


class PlanningConstraint(Base):
    """A farmer's decision about one planning condition the assistant proposed (CONTRACTS.md ›
    Constraint), for one field and planning season. Proposals are never stored: a row exists only
    after the farmer confirmed (or rejected) it. It is a planning preference for this field, not a
    measurement and not a farm-wide default; it is valid only for the boundary it was decided on."""

    __tablename__ = "field_planning_constraints"
    __table_args__ = (
        CheckConstraint("status IN ('confirmed', 'rejected')", name="status"),
        CheckConstraint(
            "source IN ('farmer_text', 'farmer_voice', 'farmer_ui', 'agent_inferred')",
            name="source",
        ),
    )

    field_id: Mapped[str] = mapped_column(
        ForeignKey("fields.id", ondelete="CASCADE"), primary_key=True
    )
    season: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(32), primary_key=True)
    boundary_revision: Mapped[int]
    # A new random token with every write (create, change, or the same decision again). A
    # replacement or a withdrawal names the token the farmer saw, so a stale tab cannot overwrite or
    # remove a newer decision, even one recreated after the seen one was removed (no ABA: a counter
    # would restart at 1 after a delete, a fresh UUID never repeats an earlier one).
    token: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16))
    value: Mapped[object] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(16))
    evidence: Mapped[str | None] = mapped_column(String(500))
    confidence: Mapped[float | None]
    decided_by: Mapped[int | None]  # the signed-in member; None in local (single-user) mode
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
