from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from field_shift_science.fields.schemas import Season
from field_shift_science.weather.scenarios import Scenario
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

AgentProviderName = Literal[
    "template", "deterministic", "scripted-llm", "gemini", "anthropic", "openai"
]


class AgentRequest(BaseModel):
    field_id: Annotated[str, StringConstraints(min_length=1, max_length=24)]
    season: Season
    scenario: Scenario
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    run_id: UUID | None = None
    conversation_id: UUID | None = None
    locale: Literal["uk", "en"] | None = None


class AgentAction(BaseModel):
    label: str
    target: Literal["water", "rotations", "fields"]
    run_id: UUID | None = None
    analysis: Literal["vegetation"] | None = None
    forecast_day: date | None = None
    layer: Literal["ndvi_variability"] | None = None
    field_revision: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def action_context(self) -> "AgentAction":
        if self.analysis is not None and self.target != "fields":
            raise ValueError("Map analysis actions must target fields.")
        if self.layer is not None and self.analysis != "vegetation":
            raise ValueError("The NDVI layer requires vegetation analysis.")
        if self.forecast_day is not None and (self.target != "water" or self.run_id is not None):
            raise ValueError("Forecast actions target water without a historical run.")
        if self.run_id is not None and self.target != "water":
            raise ValueError("Historical runs must target water.")
        return self


class AgentCitation(BaseModel):
    id: str
    title: str
    url: str
    publisher: str | None = None
    year: int | None = None


class AgentStatus(BaseModel):
    provider: AgentProviderName | None
    demo: bool
    available: bool


class AgentReplyMetadata(BaseModel):
    provider: AgentProviderName | None = None
    citations: list[AgentCitation] = Field(default_factory=list)
    options: list[str] = Field(default_factory=list)


class AgentReply(AgentReplyMetadata):
    message: str
    demo: bool
    actions: list[AgentAction]
    conversation_id: UUID


class MessageRead(AgentReplyMetadata):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: Literal["user", "assistant"]
    text: str
    demo: bool
    actions: list[AgentAction]
    created_at: datetime


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    field_id: str
    season: int
    scenario: Scenario
    title: str
    updated_at: datetime


class ConversationDetail(ConversationRead):
    messages: list[MessageRead]
