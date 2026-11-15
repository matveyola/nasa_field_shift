"""Compare operationally feasible plans by the grower's selected priority."""

from collections.abc import Sequence
from math import fsum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StringConstraints,
    model_validator,
)

from field_shift_rotation_model.modules.rotations.equipment import EQUIPMENT_IDS

EquipmentAccess = Literal["owned", "contracted", "unavailable", "unknown"]
Feasibility = Literal["feasible", "infeasible", "needs_confirmation"]
Priority = Literal[
    "water_stress",
    "water_deficit",
    "water_saving",
    "yield",
    "revenue",
    "soil_cover",
]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Nonnegative = Annotated[FiniteFloat, Field(ge=0)]


class FarmerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    equipment_access: dict[Identifier, EquipmentAccess] = Field(default_factory=dict)
    equipment_ids: list[Identifier] | None = Field(
        default=None,
        max_length=40,
        description="Completed equipment multiselect. Listed items are owned.",
    )
    irrigation_available: bool | None = None
    irrigation_system: Identifier | None = Field(
        default=None,
        description="Descriptive system name. Does not schedule irrigation.",
    )
    priorities: list[Priority] = Field(
        default_factory=list,
        max_length=1,
        description="Pass one priority for ranking or omit it for comparison without ranks.",
    )

    @model_validator(mode="after")
    def validate_equipment_selection(self):
        if self.equipment_ids is not None:
            if set(self.equipment_ids) - EQUIPMENT_IDS:
                raise ValueError("Use equipment identifiers from the forecast catalog.")
            if len(set(self.equipment_ids)) != len(self.equipment_ids):
                raise ValueError("Equipment selection cannot contain duplicates.")
        return self


class PlanEconomics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marketable_yield_t_ha: Nonnegative | None = Field(
        default=None,
        description="User-supplied marketable crop yield in tonnes per hectare.",
    )
    farmgate_price_per_t: Nonnegative | None = Field(
        default=None,
        description="User-supplied farm-gate price in the stated currency per tonne.",
    )
    included_cost_per_ha: Nonnegative | None = Field(
        default=None,
        description="Costs already included in the stated currency per hectare.",
    )
    currency: Currency | None = None
    yield_moisture_pct: Annotated[FiniteFloat, Field(ge=0, lt=100)] | None = None
    source: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]

    @model_validator(mode="after")
    def check_currency(self):
        if self.currency is None and (
            self.farmgate_price_per_t is not None or self.included_cost_per_ha is not None
        ):
            raise ValueError("Financial inputs require a three-letter currency code.")
        return self


class PlanDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Identifier
    required_equipment: list[Identifier] | None = None
    uses_irrigation: bool = False
    median_water_stress_days_pct: Nonnegative | None = None
    median_water_deficit_pct: Nonnegative | None = None
    irrigation_mm: Nonnegative | None = None
    marketable_yield_t_ha: Nonnegative | None = None
    gross_revenue_per_ha: Nonnegative | None = None
    gross_revenue_currency: Currency | None = None
    living_cover_days: Nonnegative | None = None


class PlanDecision(BaseModel):
    id: str
    feasibility: Feasibility
    blockers: list[str]
    unknowns: list[str]
    rank: int | None = None
    values: dict[Priority, float | None]
    gross_revenue_currency: Currency | None = None


class FarmerDecision(BaseModel):
    priorities_used: list[Priority]
    priorities_missing: list[Priority]
    decisions: list[PlanDecision]


def add_economics(
    seasons: Sequence[PlanEconomics | None],
) -> tuple[float | None, float | None, str | None]:
    """Return total supplied yield and same-currency margin across a complete plan."""
    supplied = [season for season in seasons if season is not None]
    if not supplied or len(supplied) != len(seasons):
        return None, None, None
    yields = [season.marketable_yield_t_ha for season in supplied]
    if any(value is None for value in yields):
        return None, None, None
    total_yield = fsum(value for value in yields if value is not None)
    currencies = {season.currency for season in supplied}
    if None in currencies or len(currencies) != 1:
        return total_yield, None, None
    margins = [
        season.marketable_yield_t_ha * season.farmgate_price_per_t - season.included_cost_per_ha
        for season in supplied
        if season.marketable_yield_t_ha is not None
        and season.farmgate_price_per_t is not None
        and season.included_cost_per_ha is not None
    ]
    if len(margins) != len(seasons):
        return total_yield, None, None
    return total_yield, fsum(margins), currencies.pop()


def _decision(farmer: FarmerSettings, plan: PlanDecisionInput) -> PlanDecision:
    blockers = []
    unknowns = []
    if plan.required_equipment is None:
        unknowns.append("equipment_requirements_not_provided")
    for requirement in dict.fromkeys(plan.required_equipment or []):
        if requirement in farmer.equipment_access:
            access = farmer.equipment_access[requirement]
        elif farmer.equipment_ids is None or requirement not in EQUIPMENT_IDS:
            access = "unknown"
        else:
            access = "owned" if requirement in farmer.equipment_ids else "unavailable"
        if access == "unavailable":
            blockers.append(f"equipment_unavailable:{requirement}")
        if access == "unknown":
            unknowns.append(f"equipment_access_unknown:{requirement}")

    if plan.uses_irrigation:
        if farmer.irrigation_available is False:
            blockers.append("irrigation_unavailable")
        if farmer.irrigation_available is None:
            unknowns.append("irrigation_access_unknown")

    return PlanDecision(
        id=plan.id,
        feasibility="infeasible" if blockers else "needs_confirmation" if unknowns else "feasible",
        blockers=blockers,
        unknowns=unknowns,
        values={
            "water_stress": plan.median_water_stress_days_pct,
            "water_deficit": plan.median_water_deficit_pct,
            "water_saving": plan.irrigation_mm,
            "yield": plan.marketable_yield_t_ha,
            "revenue": plan.gross_revenue_per_ha,
            "soil_cover": plan.living_cover_days,
        },
        gross_revenue_currency=plan.gross_revenue_currency,
    )


def _comparable(priority: Priority, feasible: list[PlanDecision]) -> bool:
    if not feasible or any(plan.values[priority] is None for plan in feasible):
        return False
    if priority != "revenue":
        return True
    currencies = {plan.gross_revenue_currency for plan in feasible}
    return None not in currencies and len(currencies) == 1


def _ordering(plan: PlanDecision, priority: Priority) -> float:
    value = plan.values[priority]
    if value is None:
        raise ValueError("Cannot rank a missing priority value.")
    if priority in ("water_stress", "water_deficit", "water_saving"):
        return value
    return -value


def rank_plans(farmer: FarmerSettings, plans: Sequence[PlanDecisionInput]) -> FarmerDecision:
    """Rank feasible plans by the selected criterion, with equal ranks for ties."""
    decisions = [_decision(farmer, plan) for plan in plans]
    outcome = FarmerDecision(priorities_used=[], priorities_missing=[], decisions=decisions)
    if not farmer.priorities:
        return outcome

    priority = farmer.priorities[0]
    feasible = [plan for plan in decisions if plan.feasibility == "feasible"]
    comparable_plans = decisions if priority == "water_stress" and feasible else feasible
    if not _comparable(priority, comparable_plans):
        outcome.priorities_missing.append(priority)
        return outcome

    ordered = sorted(feasible, key=lambda plan: _ordering(plan, priority))
    previous = None
    rank = 0
    for position, plan in enumerate(ordered, 1):
        ordering = _ordering(plan, priority)
        if ordering != previous:
            rank = position
            previous = ordering
        plan.rank = rank
    outcome.priorities_used.append(priority)
    return outcome
