from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
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
