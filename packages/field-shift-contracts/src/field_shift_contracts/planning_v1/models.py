"""Model capabilities, evaluation output and offline observation contracts."""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .common import (
    Capability,
    Checksum,
    Contract,
    CropRef,
    Finite,
    Fraction,
    Id,
    Metric,
    NonNegative,
    OutputStatus,
    Reason,
    Season,
    Text,
    TrainingStatus,
    Utc,
    Versioned,
    unique,
)


class ModelRef(Contract):
    model_id: Id
    version: Id
    artifact_sha256: Checksum
    parameter_sha256: Checksum


class ValidationCohort(Contract):
    partition: Literal["fit", "calibration", "test"]
    region_ids: Annotated[list[Id], Field(max_length=100)]
    farm_group_ids: Annotated[list[Id], Field(max_length=10000)]
    grid_group_ids: Annotated[list[Id], Field(max_length=10000)]
    crops: Annotated[list[Id], Field(max_length=100)]
    years: Annotated[list[Season], Field(max_length=200)]
    observation_count: Annotated[int, Field(ge=0)]
    metrics: dict[Id, Metric]


class ModelCard(Versioned):
    model: ModelRef
    licence_identifier: Text
    licence_source: Text
    licence_sha256: Checksum
    commercial_use: Literal["permitted", "restricted", "unverified"]
    data_permissions: Annotated[list[Text], Field(max_length=100)]
    capabilities: Annotated[list[Capability], Field(min_length=1, max_length=20)]
    supported_crops: Annotated[list[CropRef], Field(min_length=1, max_length=100)]
    supported_region_ids: Annotated[list[Id], Field(max_length=100)]
    validated_scope: Text | None
    training_status: TrainingStatus
    cohorts: Annotated[list[ValidationCohort], Field(max_length=100)]
    promotion: Literal["baseline_only", "candidate", "passed", "failed", "insufficient_evidence"]
    gate_protocol_version: Id
    gate_frozen_at: Utc
    holdout_opened_at: Utc | None
    fallback_model: ModelRef | None
    reasons: Annotated[list[Reason], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def evaluation_integrity(self):
        if self.holdout_opened_at and self.holdout_opened_at < self.gate_frozen_at:
            raise ValueError("release protocol must be frozen before opening holdout")
        for key in ("farm_group_ids", "grid_group_ids"):
            partitions = {}
            for cohort in self.cohorts:
                partitions.setdefault(cohort.partition, set()).update(getattr(cohort, key))
            names = list(partitions)
            for i, name in enumerate(names):
                if any(partitions[name] & partitions[n] for n in names[i + 1 :]):
                    raise ValueError(f"{key} overlap across evaluation partitions")
        if self.promotion == "passed" and (
            not self.validated_scope
            or self.holdout_opened_at is None
            or not any(c.partition == "test" and c.observation_count > 0 for c in self.cohorts)
        ):
            raise ValueError("promotion needs scope and independent test evidence")
        return self


class HarvestBasis(Contract):
    basis: Literal["dry_matter", "standard_moisture", "fresh_weight"]
    moisture_fraction: Fraction

    @model_validator(mode="after")
    def dry(self):
        if self.basis == "dry_matter" and self.moisture_fraction != 0:
            raise ValueError("dry matter requires zero moisture fraction")
        if self.moisture_fraction == 1:
            raise ValueError("harvest cannot be entirely water")
        return self


class SeasonEvaluation(Contract):
    season: Season
    crop: CropRef
    trajectory_id: Id
    status: OutputStatus
    yield_t_ha: Metric
    harvest_basis: HarvestBasis | None
    root_zone_reserve_mm: Metric
    root_depth_cm: NonNegative | None
    surface_moisture_m3_m3: Metric
    surface_depth_top_cm: NonNegative | None
    surface_depth_bottom_cm: NonNegative | None
    stress_days: Metric
    irrigation_mm: Metric
    opening_state_id: Id | None
    closing_state_id: Id | None
    reasons: Annotated[list[Reason], Field(max_length=100)]

    @model_validator(mode="after")
    def dimensions(self):
        for key, unit in {
            "yield_t_ha": "t/ha",
            "root_zone_reserve_mm": "mm",
            "surface_moisture_m3_m3": "m3/m3",
            "stress_days": "days",
            "irrigation_mm": "mm",
        }.items():
            metric = getattr(self, key)
            if metric.unit != unit:
                raise ValueError(f"{key} requires {unit}")
            for v in (metric.value, metric.lower, metric.upper):
                if v is not None and (v < 0 or (unit == "m3/m3" and v > 1)):
                    raise ValueError(f"invalid physical value for {key}")
                if v is not None and key == "stress_days" and v > 366:
                    raise ValueError("season stress exceeds 366 days")
        if self.yield_t_ha.status == "available" and self.harvest_basis is None:
            raise ValueError("yield requires harvest moisture basis")
        if self.root_zone_reserve_mm.status == "available" and not self.root_depth_cm:
            raise ValueError("root water requires a positive root depth")
        if self.surface_moisture_m3_m3.status == "available":
            if (
                self.surface_depth_top_cm is None
                or self.surface_depth_bottom_cm is None
                or self.surface_depth_bottom_cm <= self.surface_depth_top_cm
            ):
                raise ValueError("surface moisture requires a depth interval")
        if self.status != "available" and not self.reasons:
            raise ValueError("non-available evaluation requires reasons")
        return self


class SequenceEvaluation(Versioned):
    evaluation_id: Id
    sequence_id: Id
    field_snapshot_id: Id
    scenario_set_id: Id
    as_of: Utc
    model: ModelRef
    applicability: Literal["validated", "baseline", "experimental", "out_of_domain", "unsupported"]
    uncertainty_scope: Text
    seasons: Annotated[list[SeasonEvaluation], Field(min_length=1, max_length=192)]
    input_sha256: Checksum
    reasons: Annotated[list[Reason], Field(max_length=100)]

    @model_validator(mode="after")
    def state_carry(self):
        unique(
            [(s.season, s.trajectory_id) for s in self.seasons],
            "evaluation season/member",
        )
        for trajectory in {s.trajectory_id for s in self.seasons}:
            rows = sorted(
                (s for s in self.seasons if s.trajectory_id == trajectory),
                key=lambda s: s.season,
            )
            for before, after in zip(rows, rows[1:]):
                if after.season != before.season + 1:
                    raise ValueError("sequence evaluation must not omit seasons")
                if before.status == after.status == "available":
                    if (
                        not before.closing_state_id
                        or before.closing_state_id != after.opening_state_id
                    ):
                        raise ValueError("season transition needs explicit carried state")
        return self


class TrainingObservation(Versioned):
    observation_id: Id
    farm_group_id: Id
    field_id: Id
    geographic_group_id: Id
    grid_group_id: Id
    geometry_revision: Annotated[int, Field(ge=0)]
    region_id: Id
    crop: CropRef | None
    observed_at: Utc
    available_at: Utc
    kind: Literal["soil_moisture", "yield", "phenology", "canopy_cover", "biomass", "irrigation"]
    value: Finite | None
    unit: Literal["m3/m3", "t/ha", "stage", "1", "kg/ha", "mm"]
    depth_top_cm: NonNegative | None
    depth_bottom_cm: NonNegative | None
    crop_stage: Text | None
    sowing_date: date | None
    harvest_date: date | None
    irrigation_mm: NonNegative | None
    harvest_basis: HarvestBasis | None
    field_snapshot_id: Id | None
    source_checksum_sha256: Checksum
    source_locator: Text
    permission: Text
    quality: Literal["valid", "partial", "missing", "rejected"]
    synthetic: bool

    @model_validator(mode="after")
    def observation_shape(self):
        if self.available_at < self.observed_at:
            raise ValueError("observation availability precedes measurement")
        units = {
            "soil_moisture": "m3/m3",
            "yield": "t/ha",
            "phenology": "stage",
            "canopy_cover": "1",
            "biomass": "kg/ha",
            "irrigation": "mm",
        }
        if self.unit != units[self.kind]:
            raise ValueError("observation kind/unit mismatch")
        if self.quality == "valid" and self.value is None:
            raise ValueError("valid observation needs a value")
        if self.value is not None and (
            self.value < 0 or (self.unit in ("m3/m3", "1") and self.value > 1)
        ):
            raise ValueError("observation outside physical range")
        if self.kind == "soil_moisture" and (
            self.depth_top_cm is None
            or self.depth_bottom_cm is None
            or self.depth_bottom_cm <= self.depth_top_cm
        ):
            raise ValueError("soil moisture label needs depth interval")
        if self.kind == "yield" and (self.harvest_basis is None or self.crop is None):
            raise ValueError("yield label needs crop and moisture basis")
        if self.sowing_date and self.harvest_date and self.harvest_date < self.sowing_date:
            raise ValueError("harvest precedes sowing")
        return self
