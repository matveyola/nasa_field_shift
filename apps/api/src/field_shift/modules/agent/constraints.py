"""Farmer-confirmed planning constraints (CONTRACTS.md › Constraint), the host side.

The assistant proposes; only the farmer decides. A proposal, agent-inferred or not, is never
stored: the host records a constraint only when the farmer's confirmation (or rejection) arrives
as a `compare.set_constraints` action made for this field and its current boundary. The record is
a planning preference for one field and planning season: not a measurement, and never a farm-wide
default (farm defaults are not touched). The agent sees these records only in the field context
the API builds and signs, so a browser cannot make a constraint "confirmed" by saying so in its
context or history.

No rotation comparison or simulation reads them yet: the API never says one did.
"""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from field_shift.errors import ConflictError
from field_shift.modules.agent.models import PlanningConstraint
from field_shift.modules.fields.service import require_field

# The wire types of the agent contract (services/agent/contracts/models.py), mirrored here because
# the API does not import the agent package. Strict, like the contract: no unknown keys or coercion.
Id = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
CropId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
CropList = Annotated[list[CropId], Field(min_length=1, max_length=20)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=300)]
# The opaque token of one stored decision (a UUID, new with every write).
Token = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Constraint(_Strict):
    id: Id
    status: Literal["proposed", "confirmed", "rejected"]
    source: Literal["farmer_text", "farmer_voice", "farmer_ui", "agent_inferred"]
    evidence: Annotated[str, StringConstraints(min_length=1, max_length=500)] | None = None
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None = None

    @model_validator(mode="after")
    def _traceable(self):
        if self.source in ("farmer_text", "farmer_voice") and self.evidence is None:
            raise ValueError("a constraint from farmer text/voice needs the evidence span")
        if self.source == "agent_inferred" and self.confidence is None:
            raise ValueError("an agent-inferred constraint needs confidence")
        return self


class Irrigation(_Constraint):
    type: Literal["irrigation_available"]
    value: bool


class FertilizerBudget(_Constraint):
    type: Literal["fertilizer_budget"]
    value: Literal["minimize_n", "low", "normal"]


class ErosionProtection(_Constraint):
    type: Literal["erosion_protection"]
    value: bool


class RotationLength(_Constraint):
    type: Literal["rotation_length_years"]
    value: Annotated[int, Field(ge=2, le=6)]


class ExcludedCrops(_Constraint):
    type: Literal["excluded_crops"]
    value: CropList


class PreferredCrops(_Constraint):
    type: Literal["preferred_crops"]
    value: CropList


class PreviousCrop(_Constraint):
    type: Literal["previous_crop"]
    value: CropId


class OrganicOnly(_Constraint):
    type: Literal["organic_only"]
    value: bool


class MachineryLimit(_Constraint):
    type: Literal["machinery_limit"]
    value: ShortText  # free text: display only, never scored


class MarketPreference(_Constraint):
    type: Literal["market_preference"]
    value: CropList


Constraint = Annotated[
    Irrigation
    | FertilizerBudget
    | ErosionProtection
    | RotationLength
    | ExcludedCrops
    | PreferredCrops
    | PreviousCrop
    | OrganicOnly
    | MachineryLimit
    | MarketPreference,
    Field(discriminator="type"),
]


class SetConstraintsAction(_Strict):
    type: Literal["compare.set_constraints"]
    constraints: Annotated[list[Constraint], Field(min_length=1, max_length=10)]


class SetConstraintsEnvelope(_Strict):
    """The UIActionEnvelope the widget hands the host when the farmer confirms. Only a `chip`
    (a button the farmer pressed): an `auto` action is never a decision, so it is refused here too.
    This is defence in depth, not proof of a physical click: the signed-in browser sends it."""

    id: Id
    field_context_revision: Annotated[int, Field(ge=0)]
    trigger: Literal["chip"]
    label: ShortText
    reason: ShortText | None = None
    action: SetConstraintsAction


ConstraintType = Literal[
    "irrigation_available",
    "fertilizer_budget",
    "erosion_protection",
    "rotation_length_years",
    "excluded_crops",
    "preferred_crops",
    "previous_crop",
    "organic_only",
    "machinery_limit",
    "market_preference",
]


class Replacement(_Strict):
    """The farmer saw this exact earlier decision (its token) and chose to replace it."""

    type: ConstraintType
    token: Token


class ConstraintDecision(_Strict):
    """The farmer's decision, sent by the app for the field the assistant action was made for."""

    field_id: Annotated[str, StringConstraints(min_length=1, max_length=24)]
    envelope: SetConstraintsEnvelope
    replaces: Annotated[list[Replacement], Field(max_length=10)] = []


class ReplacementRequired(ConflictError):
    """A decision would change an earlier one the farmer has not seen being replaced."""

    def __init__(self, replacements: list[dict]):
        super().__init__("Confirm that this replaces your earlier decision.")
        self.replacements = replacements


def _contract(row: PlanningConstraint) -> dict:
    """A stored decision as a contract Constraint, with the farmer's own words."""
    constraint = {
        "id": f"pc_{row.type}",  # one decision per type: a stable id the agent can refer to
        "type": row.type,
        "value": row.value,
        "status": row.status,
        "source": row.source,
    }
    if row.evidence is not None:
        constraint["evidence"] = row.evidence
    if row.confidence is not None:
        constraint["confidence"] = row.confidence
    return constraint


def _record(row: PlanningConstraint) -> dict:
    """A stored decision for the farmer's review, with the token a change must name."""
    return {**_contract(row), "token": row.token}


def _for_agent(row: PlanningConstraint) -> dict | None:
    """A stored decision as the agent receives it in the signed field context: structured values
    only. Like scouting notes, free text stays in the farm record (the quoted words and the
    display-only machinery note), so one member's text never reaches another member's turn."""
    if row.type == "machinery_limit":
        return None
    constraint = {
        "id": f"pc_{row.type}",
        "type": row.type,
        "value": row.value,
        "status": row.status,
        # The farmer decided in the app. An inference the farmer approved stays marked as one.
        "source": "agent_inferred" if row.source == "agent_inferred" else "farmer_ui",
    }
    if row.source == "agent_inferred" and row.confidence is not None:
        constraint["confidence"] = row.confidence
    return constraint


def _current(
    session: Session, field_id: str, season: int, boundary_revision: int
) -> list[PlanningConstraint]:
    """Decisions for this field, season and current boundary; older boundaries are stale."""
    return list(
        session.scalars(
            select(PlanningConstraint)
            .where(
                PlanningConstraint.field_id == field_id,
                PlanningConstraint.season == season,
                PlanningConstraint.boundary_revision == boundary_revision,
            )
            .order_by(PlanningConstraint.type)
        )
    )


def planning_constraints(
    session: Session, field_id: str, season: int, boundary_revision: int
) -> list[dict]:
    """The farmer's decisions for review in the app, with the words they came from."""
    return [_record(row) for row in _current(session, field_id, season, boundary_revision)]


def agent_constraints(
    session: Session, field_id: str, season: int, boundary_revision: int
) -> list[dict]:
    """The farmer's decisions for the agent's signed field context (see _for_agent)."""
    rows = _current(session, field_id, season, boundary_revision)
    return [c for c in map(_for_agent, rows) if c is not None]


def decide(
    session: Session,
    field_id: str,
    season: int,
    decision: ConstraintDecision,
    decided_by: int | None,
) -> tuple[list[dict], list[dict]]:
    """Record the farmer's confirmations or rejections, all or none.

    Every check runs before anything is written. A decision that would change an earlier current
    one of the same kind needs the farmer's explicit acknowledgement of that exact earlier decision
    (`replaces`: its type and token); without it nothing is saved and the replacements are
    returned for the farmer to review. An acknowledgement of a decision that has since changed,
    or was removed and recreated, carries another token: it is stale and refused, so a decision
    the farmer never saw is never overwritten. Returns the current decisions
    and the earlier ones replaced."""
    field = require_field(session, field_id)  # the caller's farm only
    session.refresh(field, with_for_update=True)  # serializes decisions for this field
    if decision.field_id != field.id:
        raise ConflictError("This decision was made for another field.")
    if decision.envelope.field_context_revision != field.boundary_revision:
        raise ConflictError("The field boundary changed. Ask the assistant again.")
    constraints = decision.envelope.action.constraints
    if any(c.status == "proposed" for c in constraints):
        raise ConflictError("Only constraints the farmer confirmed or rejected can be saved.")
    if len({c.type for c in constraints}) != len(constraints):
        raise ConflictError("Decide each kind of constraint once.")
    acknowledged = {r.type: r.token for r in decision.replaces}
    if len(acknowledged) != len(decision.replaces) or not acknowledged.keys() <= {
        c.type for c in constraints
    }:
        raise ConflictError("Each replacement must name one kind of constraint being decided.")

    rows = {
        c.type: session.get(PlanningConstraint, (field.id, season, c.type)) for c in constraints
    }
    unconfirmed, replaced = [], []
    for constraint in constraints:
        row = rows[constraint.type]
        current = row is not None and row.boundary_revision == field.boundary_revision
        if constraint.type in acknowledged and (
            not current or row.token != acknowledged[constraint.type]
        ):
            raise ConflictError(
                "This decision changed since you reviewed it. Review the current one again."
            )
        if not current or (row.status, row.value) == (constraint.status, constraint.value):
            continue
        if constraint.type not in acknowledged:
            unconfirmed.append(
                {"current": _record(row), "proposed": constraint.model_dump(exclude_none=True)}
            )
        else:
            replaced.append(_record(row))
    if unconfirmed:
        raise ReplacementRequired(unconfirmed)  # nothing has been written

    now = datetime.now(UTC)
    for constraint in constraints:
        row = rows[constraint.type]
        if row is None:
            row = PlanningConstraint(field_id=field.id, season=season, type=constraint.type)
            session.add(row)
        row.token = str(uuid4())
        row.boundary_revision = field.boundary_revision
        row.status = constraint.status
        row.value = constraint.value
        row.source = constraint.source
        row.evidence = constraint.evidence
        row.confidence = constraint.confidence
        row.decided_by = decided_by
        row.decided_at = now
    session.commit()
    return planning_constraints(session, field.id, season, field.boundary_revision), replaced


def withdraw(
    session: Session,
    field_id: str,
    season: int,
    kind: str,
    boundary_revision: int,
    token: str,
) -> list[dict]:
    """The farmer removes the exact decision they were shown: the field's current boundary and the
    decision's token must still match, else nothing is deleted (a stale tab, a newer decision, or
    one recreated after the seen one was removed)."""
    field = require_field(session, field_id)
    session.refresh(field, with_for_update=True)  # the same lock as decisions and boundary edits
    if boundary_revision != field.boundary_revision:
        raise ConflictError("The field boundary changed. Reload before removing a decision.")
    row = session.get(PlanningConstraint, (field.id, season, kind))
    if row is None or row.boundary_revision != field.boundary_revision or row.token != token:
        raise ConflictError("This decision changed since you loaded it. Reload and try again.")
    session.delete(row)
    session.commit()
    return planning_constraints(session, field.id, season, field.boundary_revision)
