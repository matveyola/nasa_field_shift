from collections.abc import Sequence
from functools import lru_cache

from pydantic import BaseModel

from field_shift.modules.rotations.schemas import PlanId, RotationCrop, RotationPlanSchedule
from field_shift.modules.simulation.schemas import HistoricalProvenance
from field_shift.modules.simulation.service import calculate_water_balance
from field_shift.modules.simulation.water_balance import WaterInputs, stress_summary
from field_shift.modules.weather.historical import (
    HistoricalWeatherProvider,
    OpenMeteoHistoricalWeather,
)


class HistoricalPlan(BaseModel):
    id: PlanId
    name: str
    crops: list[RotationCrop]
    stress_days: int
    longest_episode: int
    sensitive_days: int | None
    min_reserve_mm: float
    provenance: HistoricalProvenance


class HistoricalRotationComparison(BaseModel):
    analysis: str = "rotation_comparison"
    field_id: str
    season: int
    scenario: str
    demo: bool = False
    evaluated_year: int
    plans: list[HistoricalPlan]


class SharedWeather:
    """Reuse an immutable weather snapshot across plans in the same job."""

    def __init__(self, provider: HistoricalWeatherProvider) -> None:
        self.load = lru_cache(maxsize=1)(provider.load)


def compare_historical_plans(
    inputs: WaterInputs,
    plans: Sequence[RotationPlanSchedule],
    *,
    provider: HistoricalWeatherProvider | None = None,
) -> HistoricalRotationComparison:
    weather = SharedWeather(provider if provider is not None else OpenMeteoHistoricalWeather())
    results: list[HistoricalPlan] = []
    for plan in plans:
        first_crop = plan.crops[0]
        run = calculate_water_balance(
            WaterInputs(
                inputs.field_id,
                first_crop.crop,
                inputs.season,
                inputs.scenario,
                inputs.irrigation_mm,
                source=inputs.source,
                geometry=inputs.geometry,
                start_date=inputs.start_date,
                end_date=inputs.end_date,
            ),
            weather_provider=weather,
        )
        provenance = run.provenance
        if provenance is None:
            raise RuntimeError("Historical plan comparison requires historical model provenance.")
        longest, sensitive = stress_summary(run)
        results.append(
            HistoricalPlan(
                id=plan.id,
                name=plan.name,
                crops=plan.crops,
                stress_days=run.stress_days,
                longest_episode=longest,
                sensitive_days=sensitive if run.flowering_start is not None else None,
                min_reserve_mm=run.min_reserve_mm,
                provenance=provenance,
            )
        )
    return HistoricalRotationComparison(
        field_id=inputs.field_id,
        season=inputs.season,
        scenario=inputs.scenario,
        evaluated_year=plans[0].crops[0].year,
        plans=results,
    )
