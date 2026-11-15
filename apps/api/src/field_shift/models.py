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
from field_shift.modules.fields.planning_profile.models import ProfileSnapshot
from field_shift.modules.fields.planning_profile.weather_archive import WeatherIssue
from field_shift.modules.onboarding.models import FieldSetup
from field_shift.modules.planning_runtime.models import (
    PlanningBundle,
    PlanningDelivery,
    PlanningHead,
)
from field_shift.modules.precision.models import PrecisionPlan
from field_shift.modules.rotations.models import RotationPreference
from field_shift.modules.rotations.scenario_planning.models import (
    PlanningApplyRow,
    PlanningCommitment,
    PlanningRunRow,
)

metadata = Base.metadata

__all__ = [
    "ProfileSnapshot",
    "WeatherIssue",
    "PlanningRunRow",
    "PlanningApplyRow",
    "PlanningCommitment",
    "PlanningBundle",
    "PlanningHead",
    "PlanningDelivery",
    "AccessThrottle",
    "Invitation",
    "LoginSession",
    "Membership",
    "User",
    "PrecisionPlan",
    "Conversation",
    "Farm",
    "Field",
    "FieldSetup",
    "Message",
    "Observation",
    "RotationPreference",
    "metadata",
]
