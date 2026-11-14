"""Register the core SQLAlchemy models for schema management."""

from field_shift.db import Base
from field_shift.modules.access.models import (
    AccessThrottle,
    Invitation,
    LoginSession,
    Membership,
    User,
)
from field_shift.modules.agent.models import Conversation, Message
from field_shift.modules.farm.models import Farm, Observation
from field_shift.modules.fields.models import Field
from field_shift.modules.precision.models import PrecisionPlan
from field_shift.modules.rotations.models import RotationPreference

metadata = Base.metadata

__all__ = [
    "AccessThrottle",
    "Invitation",
    "LoginSession",
    "Membership",
    "User",
    "PrecisionPlan",
    "Conversation",
    "Farm",
    "Field",
    "Message",
    "Observation",
    "RotationPreference",
    "metadata",
]
