"""Injected domain interfaces. Transport/authentication belongs to the owning adapter."""

from typing import Protocol

from .field import CandidateSequence, FieldProfileSnapshot, SuitabilityAssessment
from .models import ModelCard, SequenceEvaluation
from .planning import FarmPlanComparison, FarmPlanningRequest
from .weather import WeatherScenarioSet


class ProfileProvider(Protocol):
    def get_snapshot(
        self, farm_id: str, field_id: str, snapshot_id: str
    ) -> FieldProfileSnapshot: ...


class SuitabilityProvider(Protocol):
    def assess(self, request: FarmPlanningRequest) -> list[SuitabilityAssessment]: ...

    def candidates(self, request: FarmPlanningRequest) -> list[CandidateSequence]: ...


class CropModelProvider(Protocol):
    def model_card(self) -> ModelCard: ...

    def evaluate(
        self,
        profile: FieldProfileSnapshot,
        candidate: CandidateSequence,
        weather: WeatherScenarioSet,
    ) -> SequenceEvaluation: ...


class PlanSelector(Protocol):
    def compare(
        self,
        request: FarmPlanningRequest,
        candidates: list[CandidateSequence],
        evaluations: list[SequenceEvaluation],
    ) -> FarmPlanComparison: ...


class ExplanationProvider(Protocol):
    def explain(self, comparison: FarmPlanComparison, alternative_id: str) -> str:
        """Return prose only; consumer verifies evidence/numeric fidelity, no plan mutation."""
        ...
