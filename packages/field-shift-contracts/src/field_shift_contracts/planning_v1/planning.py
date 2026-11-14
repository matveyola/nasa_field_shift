"""Farm planning requests, comparisons and explicit human application."""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .common import (
    Checksum,
    Contract,
    CropRef,
    Currency,
    DateWindow,
    Finite,
    Fraction,
    Id,
    Metric,
    NonNegative,
    Reason,
    Revision,
    RunStatus,
    Season,
    SolverStatus,
    Text,
    Utc,
    Versioned,
    unique,
)
from .field import CropAssignment, FieldProfileSnapshot
from .models import HarvestBasis
from .weather import WeatherScenarioSet


class SnapshotRef(Contract):
    field_id: Id
    snapshot_id: Id
    profile_revision: Revision
    geometry_revision: Revision


class EconomicAssumption(Contract):
    assumption_id: Id
    crop: CropRef
    season: Season
    trajectory_id: Id
    price_per_t: NonNegative | None
    variable_cost_per_ha: NonNegative | None
    irrigation_cost_per_mm_ha: NonNegative | None
    currency: Currency | None
    price_date: date | None
    harvest_basis: HarvestBasis | None
    source: Text
    available_at: Utc
    user_yield_t_ha: NonNegative | None
    yield_origin: Literal["model", "user_assumption"]

    @model_validator(mode="after")
    def basis(self):
        if any(
            v is not None
            for v in (
                self.price_per_t,
                self.variable_cost_per_ha,
                self.irrigation_cost_per_mm_ha,
            )
        ):
            if self.currency is None or self.price_date is None:
                raise ValueError("economic values require currency and price date")
        if self.price_per_t is not None and self.harvest_basis is None:
            raise ValueError("price per tonne needs harvest moisture basis")
        if (self.yield_origin == "user_assumption") != (self.user_yield_t_ha is not None):
            raise ValueError("user yield must be explicit and labeled as an assumption")
        return self


class ResourceLimit(Contract):
    constraint_id: Id
    season: Season
    budget: NonNegative | None
    currency: Currency | None
    irrigation_m3: NonNegative | None

    @model_validator(mode="after")
    def budget_unit(self):
        if self.budget is not None and self.currency is None:
            raise ValueError("budget requires currency")
        return self


class CropAreaLimit(Contract):
    constraint_id: Id
    season: Season
    crop_id: Id
    min_ha: NonNegative
    max_ha: NonNegative | None
    max_share: Fraction | None

    @model_validator(mode="after")
    def interval(self):
        if self.max_ha is not None and self.min_ha > self.max_ha:
            raise ValueError("minimum crop area exceeds maximum")
        return self


class EquipmentLimit(Contract):
    constraint_id: Id
    equipment_id: Id
    window: DateWindow
    capacity_ha: NonNegative


class SolverLimits(Contract):
    max_nodes: Annotated[int, Field(ge=1, le=1000000)]
    max_seconds: Annotated[int, Field(ge=1, le=300)]
    max_candidates_per_field: Annotated[int, Field(ge=1, le=1000)]
    max_alternatives: Annotated[int, Field(ge=1, le=3)]


class FarmPlanningRequest(Versioned):
    request_id: Id
    farm_id: Id
    as_of: Utc
    start_season: Season
    season_count: Literal[1, 3]
    fields: Annotated[list[FieldProfileSnapshot], Field(min_length=1, max_length=30)]
    weather: WeatherScenarioSet
    enabled_crops: Annotated[list[CropRef], Field(min_length=1, max_length=30)]
    rulebook_version: Id
    goal: Literal["reference_margin", "worst_case_margin", "water_demand"]
    reference_trajectory_id: Id | None
    locks: Annotated[list[CropAssignment], Field(max_length=90)]
    resources: Annotated[list[ResourceLimit], Field(max_length=3)]
    crop_areas: Annotated[list[CropAreaLimit], Field(max_length=90)]
    equipment: Annotated[list[EquipmentLimit], Field(max_length=100)]
    economics: Annotated[list[EconomicAssumption], Field(max_length=5760)]
    economic_correlation_note: Text
    solver_limits: SolverLimits

    @model_validator(mode="after")
    def scope(self):
        field_ids = {f.field_id for f in self.fields}
        snapshot_ids = {f.snapshot_id for f in self.fields}
        unique([f.field_id for f in self.fields], "field")
        unique([f.snapshot_id for f in self.fields], "snapshot")
        unique(
            [(c.crop_id, c.cultivar_id, c.profile_version) for c in self.enabled_crops],
            "crop profile",
        )
        if any(f.farm_id != self.farm_id or f.as_of > self.as_of for f in self.fields):
            raise ValueError("field belongs to another farm or future snapshot")
        if self.weather.as_of > self.as_of or self.weather.stale_after <= self.as_of:
            raise ValueError("weather is future or stale at planning time")
        if any(
            {t.field_snapshot_id for t in s.traces} != snapshot_ids for s in self.weather.scenarios
        ):
            raise ValueError("weather must cover exactly the selected snapshots")
        trajectories = {s.trajectory_id for s in self.weather.scenarios}
        if (
            self.reference_trajectory_id is not None
            and self.reference_trajectory_id not in trajectories
        ):
            raise ValueError("reference trajectory is outside weather set")
        if self.goal == "reference_margin" and self.reference_trajectory_id is None:
            raise ValueError("reference-margin goal needs reference trajectory")
        seasons = set(range(self.start_season, self.start_season + self.season_count))
        if max(seasons) > 2200:
            raise ValueError("planning horizon exceeds supported seasons")
        unique([(a.field_id, a.season) for a in self.locks], "locked field/season")
        for lock in self.locks:
            if (
                not lock.locked
                or lock.field_id not in field_ids
                or lock.season not in seasons
                or lock.crop not in self.enabled_crops
            ):
                raise ValueError("lock must target an enabled field, season and crop profile")
        unique([r.season for r in self.resources], "resource season")
        unique([e.assumption_id for e in self.economics], "economic assumption")
        unique(
            [
                (
                    e.crop.crop_id,
                    e.crop.cultivar_id,
                    e.crop.profile_version,
                    e.season,
                    e.trajectory_id,
                )
                for e in self.economics
            ],
            "economic crop/season/trajectory",
        )
        unique(
            [c.constraint_id for c in [*self.resources, *self.crop_areas, *self.equipment]],
            "constraint",
        )
        for row in [*self.resources, *self.crop_areas, *self.economics]:
            if row.season not in seasons:
                raise ValueError("constraint/assumption outside planning seasons")
        for row in self.economics:
            if row.trajectory_id not in trajectories or row.crop not in self.enabled_crops:
                raise ValueError("economic assumption outside crop/scenario scope")
            if row.available_at > self.as_of:
                raise ValueError("economic assumption unavailable at as_of")
        currencies = {e.currency for e in [*self.economics, *self.resources] if e.currency}
        if len(currencies) > 1:
            raise ValueError("normalize currencies explicitly before planning")
        return self


class ScenarioEconomics(Contract):
    trajectory_id: Id
    season: Season
    currency: Currency | None
    gross_margin: Metric
    irrigation_m3: NonNegative | None
    variable_cost: NonNegative | None
    assumption_ids: Annotated[list[Id], Field(max_length=300)]

    @model_validator(mode="after")
    def money(self):
        if self.gross_margin.unit != "currency":
            raise ValueError("farm gross margin must be in currency, not currency/ha")
        if (
            self.gross_margin.status == "available" or self.variable_cost is not None
        ) and not self.currency:
            raise ValueError("available money requires currency")
        return self


class PlanAlternative(Versioned):
    alternative_id: Id
    label: Text
    assignments: Annotated[list[CropAssignment], Field(min_length=1, max_length=90)]
    sequence_ids: Annotated[list[Id], Field(min_length=1, max_length=30)]
    evaluation_ids: Annotated[list[Id], Field(max_length=30)]
    scenario_results: Annotated[list[ScenarioEconomics], Field(max_length=192)]
    objective_value: Finite | None
    objective_unit: Literal["currency", "mm_ha", "stress_days_ha"]
    reasons: Annotated[list[Reason], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def distinct(self):
        unique([(a.field_id, a.season) for a in self.assignments], "plan assignment")
        unique(self.sequence_ids, "sequence reference")
        unique(
            [(r.trajectory_id, r.season) for r in self.scenario_results],
            "scenario result",
        )
        return self


class FarmPlanComparison(Versioned):
    comparison_id: Id
    request_id: Id
    farm_id: Id
    solver_status: SolverStatus
    explored_nodes: Annotated[int, Field(ge=0)]
    elapsed_seconds: NonNegative
    alternatives: Annotated[list[PlanAlternative], Field(max_length=3)]
    snapshots: Annotated[list[SnapshotRef], Field(min_length=1, max_length=30)]
    scenario_set_id: Id
    rulebook_version: Id
    input_sha256: Checksum
    diagnostics: Annotated[list[Reason], Field(max_length=100)]

    @model_validator(mode="after")
    def solver_result(self):
        unique([a.alternative_id for a in self.alternatives], "alternative")
        if self.solver_status in ("optimal", "feasible") and not self.alternatives:
            raise ValueError("feasible solver result requires a plan")
        if self.solver_status in ("not_run", "infeasible", "error") and self.alternatives:
            raise ValueError("solver status cannot contain feasible alternatives")
        if self.solver_status in ("infeasible", "limit_reached", "error") and not self.diagnostics:
            raise ValueError("solver failure/limit requires diagnostics")
        unique([s.field_id for s in self.snapshots], "comparison field")
        fields = {s.field_id for s in self.snapshots}
        keys = []
        for plan in self.alternatives:
            if {a.field_id for a in plan.assignments} != fields:
                raise ValueError("alternative must cover all selected fields")
            keys.append(
                tuple(
                    sorted(
                        (
                            a.field_id,
                            a.season,
                            a.crop.crop_id,
                            a.crop.cultivar_id or "",
                            a.crop.profile_version or "",
                        )
                        for a in plan.assignments
                    )
                )
            )
        unique(keys, "equivalent alternative")
        return self


class PlanningRun(Versioned):
    run_id: Id
    request: FarmPlanningRequest
    input_sha256: Checksum
    status: RunStatus
    attempt: Annotated[int, Field(ge=0)]
    created_at: Utc
    updated_at: Utc
    comparison: FarmPlanComparison | None
    error: Reason | None

    @model_validator(mode="after")
    def lifecycle(self):
        if self.updated_at < self.created_at:
            raise ValueError("run update precedes creation")
        if self.status == "succeeded" and self.comparison is None:
            raise ValueError("succeeded run requires a comparison")
        if self.status != "succeeded" and self.comparison is not None:
            raise ValueError("only a succeeded run carries a final comparison")
        if self.status == "failed" and self.error is None:
            raise ValueError("failed run requires an error")
        if self.comparison and (
            self.comparison.request_id != self.request.request_id
            or self.comparison.farm_id != self.request.farm_id
            or self.comparison.input_sha256 != self.input_sha256
            or self.comparison.scenario_set_id != self.request.weather.scenario_set_id
            or self.comparison.rulebook_version != self.request.rulebook_version
        ):
            raise ValueError("comparison does not match immutable run inputs")
        return self


class ProfileWriteRequest(Versioned):
    request_id: Id
    expected_revision: Revision | None
    profile: FieldProfileSnapshot


class ApplyPlanRequest(Versioned):
    request_id: Id
    farm_id: Id
    run_id: Id
    alternative_id: Id
    expected_input_sha256: Checksum
    expected_snapshots: Annotated[list[SnapshotRef], Field(min_length=1, max_length=30)]
    confirmed_by_human: Literal[True]


class ApplyPlanResult(Versioned):
    request_id: Id
    status: Literal["applied", "already_applied", "stale", "forbidden", "infeasible", "error"]
    applied_at: Utc | None
    reasons: Annotated[list[Reason], Field(max_length=100)]

    @model_validator(mode="after")
    def outcome(self):
        if self.status in ("applied", "already_applied"):
            if self.applied_at is None:
                raise ValueError("applied result needs timestamp")
        elif self.applied_at is not None or not self.reasons:
            raise ValueError("failed apply needs null timestamp and reasons")
        return self


class ReplanRequest(Versioned):
    previous_run_id: Id
    request: FarmPlanningRequest
    preserve_commitment_ids: Annotated[list[Id], Field(max_length=90)]


class PlanningError(Versioned):
    request_id: Id | None
    code: Literal[
        "invalid_input",
        "not_found",
        "forbidden",
        "stale",
        "conflict",
        "unavailable",
        "internal",
    ]
    retryable: bool
    reasons: Annotated[list[Reason], Field(min_length=1, max_length=100)]
