"""Compare plans using daily water balances supplied by the simulation service."""

from datetime import date, timedelta
from math import fsum
from statistics import median
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from field_shift.modules.rotations.farmer_priorities import (
    FarmerDecision,
    FarmerSettings,
    Identifier,
    Nonnegative,
    PlanDecisionInput,
    PlanEconomics,
    add_economics,
    rank_plans,
)
from field_shift.modules.rotations.soil_indicators import (
    PlanSoilInputs,
    SoilSummary,
    summarize_soil,
)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BalanceDay(Input):
    date: date
    cover: Identifier | None = Field(description="Crop identifier, or null for fallow.")
    etc_mm: Nonnegative
    eta_mm: Nonnegative
    stress: StrictBool
    wet_stress: StrictBool | None = None
    irrigation_mm: Nonnegative
    gap: StrictBool | None = Field(
        default=None, description="Whether the producer filled missing daily inputs."
    )

    @model_validator(mode="after")
    def physical_day(self):
        if self.eta_mm > self.etc_mm + 1e-6:
            raise ValueError("Modeled actual use must not exceed potential use.")
        if self.cover is None and (self.stress or self.wet_stress):
            raise ValueError("A fallow day cannot have crop stress.")
        return self


class BalanceScenario(Input):
    data_kind: Literal["historical_replay", "forecast", "conditional_scenario", "test"]
    weather_source: str = Field(min_length=1, max_length=400)


DailyRows = Annotated[list[BalanceDay], Field(min_length=1, max_length=1098)]


class SuppliedPlan(Input):
    id: Identifier
    members: dict[Identifier, DailyRows] = Field(min_length=1, max_length=40)
    required_equipment: list[Identifier] | None = Field(default=None, max_length=40)
    economics: list[PlanEconomics] | None = Field(default=None, min_length=1, max_length=10)
    soil: PlanSoilInputs | None = None

    @model_validator(mode="after")
    def prepared_soil(self):
        if self.soil and self.soil.erosion_management is not None:
            raise ValueError("Supply complete soil.erosion factors for imported comparisons.")
        return self


class CompareSuppliedBalance(Input):
    field_id: Identifier
    start: date
    end: date
    model_source: str = Field(min_length=1, max_length=400)
    scenarios: dict[Identifier, BalanceScenario] = Field(min_length=1, max_length=40)
    plans: list[SuppliedPlan] = Field(min_length=2, max_length=3)
    farmer: FarmerSettings | None = None

    @model_validator(mode="after")
    def common_coverage(self):
        days = (self.end - self.start).days + 1
        if not 1 <= days <= 1098:
            raise ValueError("Supply a comparison period of 1 to 1098 days.")
        if len({plan.id for plan in self.plans}) != len(self.plans):
            raise ValueError("Plan IDs must be unique.")
        expected = [self.start + timedelta(days=i) for i in range(days)]
        for plan in self.plans:
            if plan.members.keys() != self.scenarios.keys():
                raise ValueError(f"Plan {plan.id} must provide every declared scenario.")
            schedules = {
                tuple((day.date, day.irrigation_mm) for day in rows if day.irrigation_mm)
                for rows in plan.members.values()
            }
            if len(schedules) != 1:
                raise ValueError(
                    f"Plan {plan.id} must use the same irrigation schedule in every scenario."
                )
            for scenario, rows in plan.members.items():
                if [day.date for day in rows] != expected:
                    raise ValueError(f"Plan {plan.id}, scenario {scenario}: expected all dates once, in order.")
                if not any(day.cover is not None for day in rows):
                    raise ValueError(f"Plan {plan.id}, scenario {scenario} has no crop days.")
        return self


class BalanceMetrics(BaseModel):
    crop_days: int
    demand_mm: float
    modeled_use_mm: float
    deficit_mm: float
    water_shortfall_pct: float | None
    drought_stress_days: int
    waterlogging_stress_days: int | None
    water_stress_days: int | None
    water_stress_days_pct: float | None
    filled_input_days: int
    input_gap_status_unknown_days: int


class PlanMetrics(BaseModel):
    id: str
    scenarios: dict[str, BalanceMetrics]
    median_water_shortfall_pct: float | None
    median_water_stress_days_pct: float | None
    irrigation_mm: float
    soil: SoilSummary
    marketable_yield_t_ha: float | None
    gross_revenue_per_ha: float | None
    currency: str | None
    economics: list[PlanEconomics] | None


class SuppliedBalanceResult(BaseModel):
    field_id: str
    start: date
    end: date
    model_source: str
    scenarios: dict[str, BalanceScenario]
    plans: list[PlanMetrics]
    water_stress_comparable: bool
    decision: FarmerDecision | None


def summarize_days(rows: list[BalanceDay]) -> BalanceMetrics:
    crops = [row for row in rows if row.cover is not None]
    demand = fsum(row.etc_mm for row in crops)
    use = fsum(row.eta_mm for row in crops)
    deficit = fsum(max(0.0, row.etc_mm - row.eta_mm) for row in crops)
    wet_available = all(row.wet_stress is not None for row in crops)
    combined = (
        sum(row.stress or row.wet_stress is True for row in crops)
        if wet_available
        else None
    )
    return BalanceMetrics(
        crop_days=len(crops),
        demand_mm=round(demand, 3),
        modeled_use_mm=round(use, 3),
        deficit_mm=round(deficit, 3),
        water_shortfall_pct=round(100 * deficit / demand, 1) if demand else None,
        drought_stress_days=sum(row.stress for row in crops),
        waterlogging_stress_days=(
            sum(row.wet_stress is True for row in crops) if wet_available else None
        ),
        water_stress_days=combined,
        water_stress_days_pct=(
            round(100 * combined / len(crops), 1) if combined is not None else None
        ),
        filled_input_days=sum(row.gap is True for row in rows),
        input_gap_status_unknown_days=sum(row.gap is None for row in rows),
    )


def _complete_median(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return round(median(present), 1) if len(present) == len(values) and present else None


def _economics(rows: list[PlanEconomics] | None) -> tuple[float | None, float | None, str | None]:
    if not rows:
        return None, None, None
    yield_t_ha, _, _ = add_economics(rows)
    priced = [
        (r.marketable_yield_t_ha * r.farmgate_price_per_t, r.currency)
        for r in rows
        if r.marketable_yield_t_ha is not None and r.farmgate_price_per_t is not None and r.currency
    ]
    if len(priced) == len(rows) and len({currency for _, currency in priced}) == 1:
        return yield_t_ha, round(fsum(cost for cost, _ in priced), 2), priced[0][1]
    return yield_t_ha, None, None


def compare(request: CompareSuppliedBalance) -> SuppliedBalanceResult:
    plans = []
    rank_inputs = []
    for plan in request.plans:
        scenarios = {key: summarize_days(rows) for key, rows in plan.members.items()}
        deficit = _complete_median([row.water_shortfall_pct for row in scenarios.values()])
        stress = _complete_median([row.water_stress_days_pct for row in scenarios.values()])
        irrigation = fsum(row.irrigation_mm for row in next(iter(plan.members.values())))
        soil = summarize_soil(plan.soil or PlanSoilInputs(), request.start, request.end)
        yield_t_ha, revenue, currency = _economics(plan.economics)
        plans.append(PlanMetrics(
            id=plan.id,
            scenarios=scenarios,
            median_water_shortfall_pct=deficit,
            median_water_stress_days_pct=stress,
            irrigation_mm=round(irrigation, 3),
            soil=soil,
            marketable_yield_t_ha=yield_t_ha,
            gross_revenue_per_ha=revenue,
            currency=currency,
            economics=plan.economics,
        ))
        rank_inputs.append(PlanDecisionInput(
            id=plan.id,
            required_equipment=plan.required_equipment,
            uses_irrigation=irrigation > 0,
            median_water_deficit_pct=deficit,
            median_water_stress_days_pct=stress,
            irrigation_mm=irrigation,
            marketable_yield_t_ha=yield_t_ha,
            gross_revenue_per_ha=revenue,
            gross_revenue_currency=currency,
            living_cover_days=soil.living_cover_days,
        ))
    return SuppliedBalanceResult(
        field_id=request.field_id,
        start=request.start,
        end=request.end,
        model_source=request.model_source,
        scenarios=request.scenarios,
        plans=plans,
        water_stress_comparable=all(plan.median_water_stress_days_pct is not None for plan in plans),
        decision=rank_plans(request.farmer, rank_inputs) if request.farmer is not None else None,
    )
