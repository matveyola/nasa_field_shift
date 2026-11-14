"""Typed acquisition ports. Missing services abstain; providers never synthesize data."""

from dataclasses import dataclass
from datetime import datetime
from typing import Generic, Literal, Protocol, TypeVar

from field_shift_contracts.planning_v1.common import Reason, SourceRecord
from field_shift_contracts.planning_v1.field import (
    FieldGeometry,
    HazardEvidence,
    SoilHorizon,
    TerrainRadiation,
)
from field_shift_contracts.planning_v1.weather import WeatherScenarioSet

from .evidence import reason

T = TypeVar("T")


@dataclass(frozen=True)
class ProviderResult(Generic[T]):
    status: Literal["available", "insufficient_data", "unsupported", "error"]
    value: T | None
    sources: tuple[SourceRecord, ...]
    reasons: tuple[Reason, ...] = ()

    def __post_init__(self):
        if self.status == "available":
            if self.value is None or not self.sources:
                raise ValueError("available result requires value and provenance")
        elif self.value is not None or not self.reasons:
            raise ValueError("unavailable result requires null and reasons")


class SoilProvider(Protocol):
    def soil(
        self, geometry: FieldGeometry, as_of: datetime
    ) -> ProviderResult[list[SoilHorizon]]: ...


class TerrainProvider(Protocol):
    def terrain(
        self, geometry: FieldGeometry, as_of: datetime
    ) -> ProviderResult[TerrainRadiation]: ...


class LandProvider(Protocol):
    def land(self, geometry: FieldGeometry, as_of: datetime) -> ProviderResult[str]: ...


class GaezProvider(Protocol):
    def suitability(
        self, geometry: FieldGeometry, crop_id: str, as_of: datetime
    ) -> ProviderResult[str]: ...


class WeatherProvider(Protocol):
    def scenarios(
        self, snapshot_ids: list[str], as_of: datetime
    ) -> ProviderResult[WeatherScenarioSet]: ...


class HazardProvider(Protocol):
    def hazards(
        self, geometry: FieldGeometry, as_of: datetime
    ) -> ProviderResult[list[HazardEvidence]]: ...


def missing_provider(name):
    return ProviderResult(
        "unsupported",
        None,
        (),
        (
            reason(
                "missing_provider",
                f"No configured {name} provider; no observations inferred",
            ),
        ),
    )


def check_source(source, as_of, now, max_age):
    if source.available_at > as_of or source.acquired_at > as_of:
        raise ValueError("source unavailable at planning instant")
    if now < source.retrieved_at or now - source.retrieved_at > max_age:
        raise ValueError("provider cache stale or from future")
    if source.quality not in {"valid", "partial"}:
        raise ValueError("source rejected or missing")


def inundation_impact(hazards):
    """River discharge/EFI are not hydraulic field-depth observations or loss probabilities."""
    hydraulic = [
        h
        for h in hazards
        if h.kind == "inundation" and h.indicator == "hydraulic_depth" and h.status == "available"
    ]
    return "requires_validated_crop_response" if hydraulic else "unsupported"
