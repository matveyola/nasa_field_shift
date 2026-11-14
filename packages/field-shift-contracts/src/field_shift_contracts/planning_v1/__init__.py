"""Planning-v1 canonical wire models; no provider, database or model runtime imports."""

from .common import CropRegistry, Versioned
from .field import CandidateSequence, FieldProfileSnapshot, SuitabilityAssessment
from .models import ModelCard, SequenceEvaluation, TrainingObservation
from .planning import (
    ApplyPlanRequest,
    ApplyPlanResult,
    FarmPlanComparison,
    FarmPlanningRequest,
    PlanAlternative,
    PlanningError,
    PlanningRun,
    ProfileWriteRequest,
    ReplanRequest,
)
from .weather import WeatherScenarioSet

__all__ = [
    "ApplyPlanRequest",
    "ApplyPlanResult",
    "CandidateSequence",
    "CropRegistry",
    "FieldProfileSnapshot",
    "FarmPlanComparison",
    "FarmPlanningRequest",
    "ModelCard",
    "PlanAlternative",
    "PlanningContracts",
    "PlanningError",
    "PlanningRun",
    "ProfileWriteRequest",
    "ReplanRequest",
    "SequenceEvaluation",
    "SuitabilityAssessment",
    "TrainingObservation",
    "WeatherScenarioSet",
]


class PlanningContracts(Versioned):
    """Export-only index of public DTOs, never an HTTP request body."""

    field_profile: FieldProfileSnapshot
    weather: WeatherScenarioSet
    suitability: SuitabilityAssessment
    candidate: CandidateSequence
    evaluation: SequenceEvaluation
    request: FarmPlanningRequest
    run: PlanningRun
    alternative: PlanAlternative
    comparison: FarmPlanComparison
    model_card: ModelCard
    observation: TrainingObservation
    crop_registry: CropRegistry
    profile_write: ProfileWriteRequest
    apply_request: ApplyPlanRequest
    apply_result: ApplyPlanResult
    replan: ReplanRequest
    error: PlanningError
