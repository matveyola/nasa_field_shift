from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from math import fsum
from statistics import mean, median
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from field_shift.modules.crops.forecast_phenology import (
    BASE_C,
    FLOWERING_END_GDD,
    FLOWERING_START_GDD,
    MAIZE_COEFFICIENTS,
    MAIZE_STAGE_LENGTHS,
    UPPER_C,
    growing_degree_days,
    stage_coefficient,
)
from field_shift.modules.rotations.historical_fields import FieldDataset, load_fields
from field_shift.modules.simulation.forecast_soil import soil_water_from_layers
from field_shift.modules.simulation.water_balance import fao56_days

FIELD_ID = "marsden_demo"
HOT_DAY_THRESHOLD_C = 32
FAO_WATER_SOURCE = "https://www.fao.org/4/X0490E/x0490e0e.htm"
FAO_YIELD_SOURCE = "https://www.fao.org/4/i2800e/i2800e.pdf"
FERTILIZER_SOURCE = "https://crops.extension.iastate.edu/files/article/PM1688.pdf"


class Scenario(StrEnum):
    BASELINE = "baseline"
    DROUGHT = "drought"
    HEAT = "heat"


RotationId = Literal["marsden-a", "marsden-b"]
CropId = Literal["maize_grain", "sunflower"]


class Crop(BaseModel):
    id: CropId
    number: int
    name: str


class Plan(BaseModel):
    id: RotationId
    number: int
    name: str
    crop_ids: tuple[CropId, CropId]


class Condition(BaseModel):
    id: Scenario
    label: str
    criterion: str


class HistoricalCondition(Condition):
    analog_year: int
    replay_years: tuple[int, int]
    rain_mm: float
    hot_days: int
    mean_tmax_c: float
    weather_adjustment: Literal["none"] = "none"


class FieldSummary(BaseModel):
    id: str
    name: str
    latitude: float
    longitude: float
    grid_latitude: float
    grid_longitude: float
    location_source_url: str | None
    soil_source_url: str
    weather_source_url: str
    reference_years: list[int]


class Catalog(BaseModel):
    field_id: str = FIELD_ID
    fields: list[FieldSummary]
    crops: list[Crop]
    rotations: list[Plan]
    scenarios: list[Condition]


class AssessmentRequest(BaseModel):
    field_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,47}$")
    rotation_ids: list[RotationId] = Field(min_length=2, max_length=2)
    scenarios: list[Scenario] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_selections(self):
        if len(set(self.rotation_ids)) != len(self.rotation_ids):
            raise ValueError("Select two distinct rotations.")
        if len(set(self.scenarios)) != len(self.scenarios):
            raise ValueError("Select distinct scenarios.")
        return self


class PercentMetric(BaseModel):
    value_pct: float | None
    method: Literal["formula", "model", "unavailable"]
    definition: str
    accuracy: Literal["not_validated_for_field", "unavailable"]
    validation_error_pct_points: float | None = None
    source_url: str | None = None


class Fertilizer(BaseModel):
    n_kg_ha: float | None = None
    p2o5_kg_ha: float | None = None
    k2o_kg_ha: float | None = None
    method: Literal["unavailable"] = "unavailable"
    accuracy: Literal["unavailable"] = "unavailable"
    reason: str = (
        "Requires soil tests, a regional rate method, and crop and management inputs."
    )
    source_url: str = FERTILIZER_SOURCE


class CropSeason(BaseModel):
    year: int
    crop_id: CropId
    planting_date: date
    flowering_start: date | None
    flowering_end: date | None
    flowering_days: int
    flowering_stress_days: int
    water_shortfall: PercentMetric
    modeled_yield_loss_due_to_water: PercentMetric


class PlanResult(BaseModel):
    rotation: Plan
    water_shortfall: PercentMetric
    modeled_yield_loss_due_to_water: PercentMetric
    yield_risk: PercentMetric
    fertilizer: Fertilizer
    crop_seasons: list[CropSeason]


class ScenarioResult(BaseModel):
    scenario: HistoricalCondition
    rotations: list[PlanResult]


class ReplayRange(BaseModel):
    minimum_pct: float
    median_pct: float
    maximum_pct: float
    method: Literal["formula", "model"]
    accuracy: Literal["not_validated_for_field"] = "not_validated_for_field"


class ProjectionPlan(BaseModel):
    rotation_id: RotationId
    water_shortfall: ReplayRange
    modeled_yield_loss_due_to_water: ReplayRange


class Projection(BaseModel):
    method: Literal["historical_replay_range"] = "historical_replay_range"
    window_count: int
    rotations: list[ProjectionPlan]


class AssessmentResult(BaseModel):
    field_id: str
    field: FieldSummary
    dataset_id: str
    reference_years: list[int]
    assumptions: list[str]
    results: list[ScenarioResult]
    projection: Projection


@dataclass(frozen=True, slots=True)
class CropParameters:
    root_depth_m: float
    depletion_fraction: float
    stage_lengths: tuple[int, int, int, int]
    coefficients: tuple[float, float, float]
    base_c: float
    upper_c: float
    flowering_gdd: tuple[int, int]
    yield_factor: float


_CROPS = [
    Crop(id="maize_grain", number=1, name="Grain maize"),
    Crop(id="sunflower", number=2, name="Sunflower"),
]
_PLANS = {
    "marsden-a": Plan(
        id="marsden-a",
        number=1,
        name="Maize → Sunflower",
        crop_ids=("maize_grain", "sunflower"),
    ),
    "marsden-b": Plan(
        id="marsden-b",
        number=2,
        name="Sunflower → Maize",
        crop_ids=("sunflower", "maize_grain"),
    ),
}
_PARAMETERS = {
    "maize_grain": CropParameters(
        1.0,
        0.55,
        MAIZE_STAGE_LENGTHS,
        MAIZE_COEFFICIENTS,
        BASE_C,
        UPPER_C,
        (FLOWERING_START_GDD, FLOWERING_END_GDD),
        1.25,
    ),
    "sunflower": CropParameters(
        root_depth_m=0.8,
        depletion_fraction=0.45,
        stage_lengths=(25, 35, 45, 25),
        # FAO-56 Table 12 uses the upper mid-season value for a dense stand.
        coefficients=(0.35, 1.15, 0.35),
        base_c=4,
        upper_c=30,
        flowering_gdd=(1266, 1616),
        yield_factor=0.95,
    ),
}
_CONDITIONS = {
    Scenario.BASELINE: Condition(
        id=Scenario.BASELINE,
        label="Typical recent historical season",
        criterion="Nearest to median rainfall and hot-day count among the remaining seasons.",
    ),
    Scenario.DROUGHT: Condition(
        id=Scenario.DROUGHT,
        label="Historical dry analog",
        criterion="Lowest May–September rainfall among eligible years.",
    ),
    Scenario.HEAT: Condition(
        id=Scenario.HEAT,
        label="Historical hot analog",
        criterion="Most days at or above 32°C after reserving the dry analog, then highest mean Tmax.",
    ),
}


@dataclass(frozen=True, slots=True)
class SeasonWeatherSummary:
    year: int
    rain_mm: float
    hot_days: int
    mean_tmax_c: float


def _field_summary(field: FieldDataset) -> FieldSummary:
    return FieldSummary(
        id=field.id,
        name=field.name,
        latitude=field.latitude,
        longitude=field.longitude,
        grid_latitude=field.grid_latitude,
        grid_longitude=field.grid_longitude,
        location_source_url=field.location_source_url,
        soil_source_url=field.soil_source_url,
        weather_source_url=field.weather_source_url,
        reference_years=sorted(
            year for year in field.seasons if year - 1 in field.seasons
        ),
    )


def assessment_catalog() -> Catalog:
    return Catalog(
        fields=[_field_summary(field) for field in load_fields().values()],
        crops=_CROPS,
        rotations=list(_PLANS.values()),
        scenarios=list(_CONDITIONS.values()),
    )


def _select_analogs(
    field: FieldDataset, years: list[int]
) -> dict[Scenario, HistoricalCondition]:
    summaries = []
    for year in years:
        days = field.seasons[year].days
        summaries.append(
            SeasonWeatherSummary(
                year,
                fsum(day.rain_mm for day in days),
                sum(day.high_c >= HOT_DAY_THRESHOLD_C for day in days),
                mean(day.high_c for day in days),
            )
        )
    dry = min(summaries, key=lambda item: (item.rain_mm, -item.year))
    hot = max(
        (item for item in summaries if item.year != dry.year),
        key=lambda item: (item.hot_days, item.mean_tmax_c, item.year),
    )
    rains = [item.rain_mm for item in summaries]
    counts = [item.hot_days for item in summaries]
    middle_rain, middle_hot = median(rains), median(counts)
    rain_span = max(rains) - min(rains) or 1
    hot_span = max(counts) - min(counts) or 1

    def distance(item: SeasonWeatherSummary) -> tuple[float, int]:
        rain_distance = abs(item.rain_mm - middle_rain) / rain_span
        heat_distance = abs(item.hot_days - middle_hot) / hot_span
        return rain_distance + heat_distance, -item.year

    typical = min(
        (item for item in summaries if item.year not in (dry.year, hot.year)),
        key=distance,
    )
    return {
        scenario: HistoricalCondition(
            **_CONDITIONS[scenario].model_dump(),
            analog_year=item.year,
            replay_years=(item.year - 1, item.year),
            rain_mm=round(item.rain_mm, 1),
            hot_days=item.hot_days,
            mean_tmax_c=round(item.mean_tmax_c, 2),
        )
        for scenario, item in zip(Scenario, (typical, dry, hot), strict=True)
    }


def _water_metric(percent: float) -> PercentMetric:
    return PercentMetric(
        value_pct=round(percent, 1),
        method="formula",
        accuracy="not_validated_for_field",
        definition="Percentage of potential crop water demand unmet in this scenario.",
        source_url=FAO_WATER_SOURCE,
    )


def _yield_metric(percent: float, *, plan: bool = False) -> PercentMetric:
    definition = "Seasonal FAO yield loss relative to water-unlimited yield."
    if plan:
        definition = "Equal-weight mean of the two seasonal water-related yield loss percentages."
    return PercentMetric(
        value_pct=round(percent, 1),
        method="model",
        accuracy="not_validated_for_field",
        definition=definition,
        source_url=FAO_YIELD_SOURCE,
    )


SeasonAssessment = tuple[CropSeason, float, float, float]


def _assess_season(field: FieldDataset, crop_id: CropId, year: int) -> SeasonAssessment:
    crop = _PARAMETERS[crop_id]
    season = field.seasons[year]
    layers = tuple(
        (layer.top_m, layer.bottom_m, layer.field_capacity, layer.wilting_point)
        for layer in field.soil_layers
    )
    soil = soil_water_from_layers(season.initial_theta, crop.root_depth_m, layers)
    weather = season.days[: sum(crop.stage_lengths)]
    coefficients = {
        day.date: stage_coefficient(index, crop.stage_lengths, crop.coefficients)
        for index, day in enumerate(weather, 1)
    }
    threshold = (1 - crop.depletion_fraction) * soil.capacity_mm
    balance = fao56_days(
        weather,
        initial_mm=soil.initial_reserve_mm,
        capacity_mm=soil.capacity_mm,
        threshold_mm=threshold,
        coefficient=crop.coefficients[1],
        daily_coefficients=coefficients,
    )
    demand = sum(day.reference_et0_mm * coefficients[day.date] for day in weather)
    deficit = max(0.0, demand - sum(day.water_use_mm for day in balance))
    shortfall_pct = 100 * deficit / demand
    yield_loss = min(100, crop.yield_factor * shortfall_pct)
    thermal_sum = 0.0
    flowering_days = []
    for weather_day, balance_day in zip(weather, balance, strict=True):
        thermal_sum += growing_degree_days(
            weather_day.low_c, weather_day.high_c, crop.base_c, crop.upper_c
        )
        if crop.flowering_gdd[0] < thermal_sum <= crop.flowering_gdd[1]:
            flowering_days.append(balance_day)
    return (
        CropSeason(
            year=year,
            crop_id=crop_id,
            planting_date=weather[0].date,
            flowering_start=flowering_days[0].date if flowering_days else None,
            flowering_end=flowering_days[-1].date if flowering_days else None,
            flowering_days=len(flowering_days),
            flowering_stress_days=sum(
                day.reserve_mm < threshold for day in flowering_days
            ),
            water_shortfall=_water_metric(shortfall_pct),
            modeled_yield_loss_due_to_water=_yield_metric(yield_loss),
        ),
        demand,
        deficit,
        yield_loss,
    )


def _assess_plan(
    plan: Plan,
    years: tuple[int, int],
    seasons_by_crop: dict[tuple[CropId, int], SeasonAssessment],
) -> PlanResult:
    seasons, demands, deficits, losses = zip(
        *[
            seasons_by_crop[crop_id, year]
            for year, crop_id in zip(years, plan.crop_ids, strict=True)
        ],
        strict=True,
    )
    return PlanResult(
        rotation=plan,
        crop_seasons=list(seasons),
        water_shortfall=_water_metric(100 * sum(deficits) / sum(demands)),
        modeled_yield_loss_due_to_water=_yield_metric(mean(losses), plan=True),
        yield_risk=PercentMetric(
            value_pct=None,
            method="unavailable",
            accuracy="unavailable",
            definition="Probability of a yield loss above a specified threshold. "
            "Requires local yield data, an event threshold and model validation.",
        ),
        fertilizer=Fertilizer(),
    )


def _replay_range(
    values: list[float], method: Literal["formula", "model"]
) -> ReplayRange:
    return ReplayRange(
        minimum_pct=min(values),
        median_pct=round(median(values), 1),
        maximum_pct=max(values),
        method=method,
    )


def assess(request: AssessmentRequest, field: FieldDataset) -> AssessmentResult:
    summary = _field_summary(field)
    reference_years = summary.reference_years
    analogs = _select_analogs(field, reference_years)
    seasons = {
        (crop.id, year): _assess_season(field, crop.id, year)
        for crop in _CROPS
        for year in field.seasons
    }
    replays = {
        year: [
            _assess_plan(_PLANS[plan_id], (year - 1, year), seasons)
            for plan_id in request.rotation_ids
        ]
        for year in reference_years
    }
    projections = []
    for index, plan_id in enumerate(request.rotation_ids):
        plans = [replays[year][index] for year in reference_years]
        projections.append(
            ProjectionPlan(
                rotation_id=plan_id,
                water_shortfall=_replay_range(
                    [plan.water_shortfall.value_pct for plan in plans], "formula"
                ),
                modeled_yield_loss_due_to_water=_replay_range(
                    [plan.modeled_yield_loss_due_to_water.value_pct for plan in plans],
                    "model",
                ),
            )
        )
    return AssessmentResult(
        field_id=request.field_id,
        field=summary,
        dataset_id=f"{field.id}-era5-seamless-{min(field.seasons)}-{max(field.seasons)}",
        reference_years=reference_years,
        assumptions=[
            "Historical ERA5-Seamless gridded reanalysis via Open-Meteo, May–September, UTC.",
            "Both plans replay the same consecutive years ending in the selected analog year.",
            "The planting date is May 1 in each year.",
            "Each season opens from April 30 ERA5-Land moisture and the field's soil profile.",
            "The model resets soil water between years.",
            "The bucket uses fixed crop root depths and assumes no irrigation or runoff.",
            "Sunflower potential water demand assumes a dense stand (FAO-56 mid-season Kc 1.15).",
            "Weather is replayed as supplied. Yield loss covers the effect of water deficit.",
            "The projection describes a range of estimates across recent historical windows.",
        ],
        results=[
            ScenarioResult(
                scenario=analogs[scenario],
                rotations=replays[analogs[scenario].analog_year],
            )
            for scenario in request.scenarios
        ],
        projection=Projection(window_count=len(reference_years), rotations=projections),
    )
