"""Forecast one decision at a time and compare complete climate rotation plans."""

import json
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from math import fsum
from os import getenv
from pathlib import Path
from statistics import median
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from field_shift_rotation_model.modules.rotations import climate_balance, climate_weather
from field_shift_rotation_model.modules.rotations.climate_economics import (
    FinanceInputs,
    SeedCostInput,
    season_economics,
)
from field_shift_rotation_model.modules.rotations.climate_reference import load_thermal_reference
from field_shift_rotation_model.modules.rotations.equipment import recommended_equipment
from field_shift_rotation_model.modules.rotations.farmer_priorities import (
    FarmerSettings,
    Identifier,
    PlanDecisionInput,
    rank_plans,
)
from field_shift_rotation_model.modules.rotations.global_country import country_at
from field_shift_rotation_model.modules.rotations.global_defaults import (
    EROSIVITY_SOURCE_URL,
    rainfall_erosivity,
)
from field_shift_rotation_model.modules.rotations.global_soil import fetch_soil_profile
from field_shift_rotation_model.modules.rotations.historical_fields import (
    Latitude,
    Longitude,
    SoilLayer,
    load_fields,
)
from field_shift_rotation_model.modules.rotations.hybrid_profiles import (
    StageLengths,
    strategy_hybrids,
)
from field_shift_rotation_model.modules.rotations.marsden_defaults import MARSDEN_COORDINATES
from field_shift_rotation_model.modules.rotations.rotation_agronomy import (
    AgronomySeason,
    DocumentedHybrid,
    HarvestedYield,
    assess_rotation_agronomy,
)
from field_shift_rotation_model.modules.rotations.soil_indicators import (
    PlanSoilInputs,
    RusleInputs,
    summarize_soil,
)

PACKAGE = files("field_shift_rotation_model")
IOWA_BUILTIN_FIELDS = frozenset({"marsden_demo", "sutherland_nw", "crawfordsville_se"})
CLIMATE_PRIORITIES = (
    "water_deficit",
    "drought_stress",
    "water_stress",
    "water_saving",
    "yield",
    "revenue",
    "revenue_after_seed",
    "soil_cover",
)
ClimatePriority = Literal[
    "water_deficit",
    "drought_stress",
    "water_stress",
    "water_saving",
    "yield",
    "revenue",
    "revenue_after_seed",
    "soil_cover",
]
StrategyMode = Literal["sequence", "compare"]


class UnknownClimateField(LookupError):
    pass


class ClimateModelUnavailable(RuntimeError):
    pass


def model_is_configured() -> bool:
    return (
        not getenv("FIELDSHIFT_RUSLAN_DIR")
        and (climate_balance.engine_directory() / "balance.py").is_file()
    )


def require_engine_directory() -> Path:
    directory = climate_balance.engine_directory()
    if not model_is_configured():
        raise ClimateModelUnavailable(
            "Installed pinned engine unavailable; FIELDSHIFT_RUSLAN_DIR overrides are unsupported."
        )
    return directory


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClimateSoilLayer(SoilLayer):
    model_config = ConfigDict(extra="forbid")
    saturation: Annotated[float, Field(gt=0, le=1)] | None = None

    @model_validator(mode="after")
    def check_saturation(self):
        if self.saturation is not None and self.saturation <= self.field_capacity:
            raise ValueError("Soil saturation must exceed field capacity.")
        return self


class ClimateLocation(Input):
    latitude: Latitude
    longitude: Longitude
    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None


class ClimateFarmer(FarmerSettings):
    priorities: list[ClimatePriority] = Field(default_factory=list, max_length=1)


class ClimateChoice(Input):
    crop_id: Identifier
    choice_id: Identifier | None = None
    calendar_id: Identifier | None = None
    stage_lengths: StageLengths | None = None
    stage_lengths_source: Annotated[str, Field(min_length=1, max_length=400)] | None = None
    hybrid: DocumentedHybrid | None = None
    economics: FinanceInputs | None = None
    seed_cost: SeedCostInput | None = None
    harvested_yield: HarvestedYield | None = None
    erosion: RusleInputs | None = None

    @model_validator(mode="after")
    def single_calendar(self):
        hybrid_lengths = self.hybrid.stage_lengths if self.hybrid else None
        if (
            sum(
                value is not None
                for value in (self.calendar_id, self.stage_lengths, hybrid_lengths)
            )
            > 1
        ):
            raise ValueError("Supply only one crop calendar or set of stage lengths.")
        if (self.stage_lengths is None) != (self.stage_lengths_source is None):
            raise ValueError("Custom stage lengths require their source.")
        return self

    def engine_input(self) -> dict:
        data = self.model_dump(
            include={
                "crop_id",
                "choice_id",
                "calendar_id",
                "stage_lengths",
                "stage_lengths_source",
            },
            exclude_none=True,
        )
        if isinstance(self, ClimateSelection):
            data["planting_date"] = self.planting_date
        if self.hybrid and self.hybrid.stage_lengths:
            data.update(
                stage_lengths=self.hybrid.stage_lengths, stage_lengths_source=self.hybrid.source_url
            )
        return data


class ClimateSelection(ClimateChoice):
    planting_date: date


def catalog_hybrid_choice(choice_id: str, preset: dict) -> ClimateChoice:
    return ClimateChoice(
        crop_id=preset["crop_id"],
        choice_id=choice_id,
        hybrid=DocumentedHybrid(name=preset["name"], source_url=preset["source"]),
    )


class ClimateContext(Input):
    field_id: Identifier | None = None
    field: ClimateLocation | None = None
    soil_layers: list[ClimateSoilLayer] | None = Field(default=None, min_length=1, max_length=12)
    soil_source: Annotated[str, Field(min_length=1, max_length=400)] | None = None
    models: list[Literal["ACCESS-CM2", "MPI-ESM1-2-HR"]] = Field(
        default_factory=lambda: ["ACCESS-CM2"], min_length=1, max_length=2
    )
    scenarios: list[Literal["ssp245", "ssp585"]] = Field(
        default_factory=lambda: ["ssp245", "ssp585"], min_length=1, max_length=2
    )
    farmer: ClimateFarmer = Field(default_factory=ClimateFarmer)
    observed_crop_history: list[AgronomySeason] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def check_inputs(self):
        if (self.field_id is None) == (self.field is None):
            raise ValueError("Supply exactly one field_id or field with coordinates.")
        if (self.soil_layers is None) != (self.soil_source is None):
            raise ValueError("Supplied soil layers require their source.")
        if len(set(self.models)) != len(self.models) or len(set(self.scenarios)) != len(
            self.scenarios
        ):
            raise ValueError("Each climate model and pathway can be selected once.")
        if self.soil_layers:
            top = 0.0
            for layer in self.soil_layers:
                if abs(layer.top_m - top) > 1e-6:
                    raise ValueError("Soil layers must be adjacent, starting at the surface.")
                top = layer.bottom_m
            if top < 1.0:
                raise ValueError("Supply soil water retention for at least the first metre.")
            saturation_count = sum(layer.saturation is not None for layer in self.soil_layers)
            if saturation_count not in (0, len(self.soil_layers)):
                raise ValueError(
                    "Provide saturation for all soil layers or leave it absent from all."
                )
        return self


def validate_planting(planting_date: date, *, strategy=False):
    # One-year weather spin-up follows the 1991–2020 thermal reference.
    first_year = 2022 if strategy else 2040
    if not first_year <= planting_date.year <= 2049:
        raise ValueError(f"Choose a planting date from {first_year} to 2049.")


def validate_history_dates(history, dates, *, allow_same_year=False):
    years = [day.year for day in dates]
    if allow_same_year and dates != sorted(set(dates)):
        raise ValueError("Provide sowing dates in chronological order.")
    if not allow_same_year and years != sorted(set(years)):
        raise ValueError("Provide decisions in chronological order, with one crop per year.")
    if len({row.season_year for row in history}) != len(history) or any(
        row.season_year >= years[0] for row in history
    ):
        raise ValueError(
            "Observed history must contain distinct years before the first modeled planting."
        )


class StrategyIrrigation(Input):
    status: Literal["unknown", "none", "available"] = "unknown"
    daily_mm: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None
    method: Annotated[str, Field(min_length=1, max_length=40)] | None = None

    @model_validator(mode="after")
    def check_capacity(self):
        if self.status != "available" and (self.daily_mm is not None or self.method is not None):
            raise ValueError("Irrigation details require available irrigation.")
        return self


class ClimateStepRequest(ClimateContext):
    planting_date: date
    strategy_mode: StrategyMode | None = None
    irrigation: StrategyIrrigation | None = None
    selected: list[ClimateSelection] = Field(
        default_factory=list, max_length=climate_balance.MAX_STRATEGY_STEPS - 1
    )
    candidates: list[ClimateChoice] = Field(
        default_factory=lambda: [
            ClimateChoice(crop_id=crop)
            for crop in (
                "maize_grain",
                "sunflower",
                "soybeans",
                "wheat_spring",
                "barley",
            )
        ],
        min_length=1,
        max_length=climate_balance.MAX_STRATEGY_CANDIDATES,
    )

    @model_validator(mode="after")
    def check_step(self):
        strategy = self.strategy_mode is not None
        for day in [*(choice.planting_date for choice in self.selected), self.planting_date]:
            validate_planting(day, strategy=strategy)
        climate_balance.required_weather_dates(
            self.planting_date,
            [choice.engine_input() for choice in self.selected],
            strategy=strategy,
        )
        validate_history_dates(
            self.observed_crop_history,
            [*(p.planting_date for p in self.selected), self.planting_date],
            allow_same_year=self.strategy_mode == "sequence",
        )
        candidates = (
            self.candidate_choices() if self.strategy_mode == "compare" else self.candidates
        )
        if len({choice.choice_id or choice.crop_id for choice in candidates}) != len(candidates):
            raise ValueError(
                "Use distinct choice_id values when comparing options of the same crop."
            )
        if self.strategy_mode is None:
            if len(self.candidates) > 8 or self.irrigation is not None:
                raise ValueError(
                    "The legacy step supports up to eight candidates and no irrigation."
                )
            return self
        if self.strategy_mode == "sequence":
            if "candidates" in self.model_fields_set:
                raise ValueError("A sequence uses the model's supported crop catalogue.")
        elif self.selected or len(self.candidates) != 2:
            raise ValueError(
                "Compare two distinct options without any previous scenario selections."
            )
        for choice in (*self.selected, *candidates):
            if choice.crop_id not in climate_balance.STRATEGY_CROPS:
                raise ValueError(f"Unsupported strategy crop: {choice.crop_id}.")
            if choice.stage_lengths is not None:
                raise ValueError("Strategy crops use standard sourced calendars.")
            if choice.choice_id not in (None, choice.crop_id) or choice.hybrid is not None:
                preset = strategy_hybrids().get(choice.choice_id)
                if (
                    preset is None
                    or preset["crop_id"] != choice.crop_id
                    or choice.calendar_id is not None
                    or choice.hybrid
                    != DocumentedHybrid(name=preset["name"], source_url=preset["source"])
                ):
                    raise ValueError("Select a supported hybrid from the strategy catalogue.")
        return self

    def candidate_choices(self):
        if self.strategy_mode == "sequence":
            return [
                *(ClimateChoice(crop_id=crop) for crop in climate_balance.STRATEGY_CROPS),
                *(
                    catalog_hybrid_choice(choice_id, preset)
                    for choice_id, preset in strategy_hybrids().items()
                ),
            ]
        if self.strategy_mode == "compare":
            presets = strategy_hybrids()
            return [
                catalog_hybrid_choice(choice.crop_id, presets[choice.crop_id])
                if choice.crop_id in presets and choice.model_fields_set == {"crop_id"}
                else choice
                for choice in self.candidates
            ]
        return self.candidates


class ClimatePlan(Input):
    id: Identifier
    seasons: list[ClimateSelection] = Field(min_length=2, max_length=3)


class ClimatePlansRequest(ClimateContext):
    plans: list[ClimatePlan] = Field(min_length=2, max_length=3)

    @model_validator(mode="after")
    def comparable_timelines(self):
        dates = [season.planting_date for season in self.plans[0].seasons]
        for day in dates:
            validate_planting(day)
        validate_history_dates(self.observed_crop_history, dates)
        if len({plan.id for plan in self.plans}) != len(self.plans) or any(
            [season.planting_date for season in plan.seasons] != dates for plan in self.plans
        ):
            raise ValueError(
                "Plans need distinct IDs and the same planting dates for paired comparisons."
            )
        return self


class ClimateRanking(BaseModel):
    priority: ClimatePriority
    status: Literal["ranked", "priority_unavailable"]
    rank_metric: str
    ranked_ids: list[str]
    selection_ids: list[str]
    selection_basis: Literal["chosen_priority", "eligible_input_order"]


class ClimateCandidateResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    crop_id: str
    choice_id: str
    status: Literal["ok", "unsupported", "unavailable"]
    reason: str | None = None
    scenarios: dict[str, dict]
    choice: ClimateChoice | ClimateSelection
    water: dict[str, float | None]
    economics: dict
    agronomy: dict
    erosion: dict
    required_equipment: list[str]
    rank: int | None = None


class ClimatePlanResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    status: Literal["ok", "unsupported"]
    reason: str | None
    seasons: list[ClimateCandidateResult]
    scenarios: dict[str, dict]
    water: dict[str, float | None]
    economics: dict
    required_equipment: list[str]
    rank: int | None = None


class ClimateResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    field: dict
    data_kind: Literal["conditional_scenario"]
    weather_dataset: str
    weather_cases: dict[str, dict]
    model_source: str
    thermal_reference: dict
    assumptions: dict
    water_stress_comparable: bool
    ranking: ClimateRanking


class ClimateStepResult(ClimateResult):
    step: int
    max_steps: int
    planting_date: date
    selected: list[ClimateSelection]
    selected_water_by_scenario: dict[str, list[dict]]
    candidates: list[ClimateCandidateResult]
    top_candidates: list[ClimateCandidateResult]


class ClimatePlansResult(ClimateResult):
    plans: list[ClimatePlanResult]


@dataclass(frozen=True)
class FieldInputs:
    details: dict
    soil_layers: list[dict]
    soil_source: str
    calendar: dict
    weather: dict
    ruslan_dir: Path


def resolve_inputs(request: ClimateContext, planting_date: date, selected) -> FieldInputs:
    is_strategy = isinstance(request, ClimateStepRequest) and request.strategy_mode is not None
    start, end = climate_balance.required_weather_dates(
        planting_date,
        [s.engine_input() for s in selected],
        strategy=is_strategy,
    )
    ruslan_dir = require_engine_directory()
    config = load_fields().get(request.field_id) if request.field_id else None
    if request.field_id and config is None:
        raise UnknownClimateField("Unknown field_id. Check the climate catalog.")
    location = config or request.field
    latitude, longitude = location.latitude, location.longitude
    if not 0 < latitude <= 70:
        raise ValueError(
            "This water-balance release supports Northern Hemisphere spring "
            "and summer crops, south of 70° N."
        )
    marsden = (latitude, longitude) == MARSDEN_COORDINATES
    if request.soil_layers is not None:
        layers = [layer.model_dump(exclude_none=True) for layer in request.soil_layers]
        soil_source = request.soil_source
    elif marsden:
        ssurgo = json.loads((PACKAGE / "examples/marsden-ssurgo-water-stress.json").read_bytes())
        layers = [
            row | {"saturation": wet["saturation"]}
            for row, wet in zip(
                ssurgo["field"]["soil_layers"],
                ssurgo["waterlogging_soil"]["layers"],
                strict=True,
            )
        ]
        soil_source = ssurgo["waterlogging_soil"]["source"]
    else:
        profile = config or fetch_soil_profile(latitude, longitude)
        layers = [
            layer.model_dump() for layer in (config.soil_layers if config else profile.layers)
        ]
        soil_source = config.soil_source_url if config else profile.source_url
    country = country_at(latitude, longitude)
    region = "US-IA" if marsden or request.field_id in IOWA_BUILTIN_FIELDS else None
    details = {
        "id": request.field_id,
        "name": location.name,
        "latitude": latitude,
        "longitude": longitude,
        "country_code": country,
        "agronomy_region_code": region,
        "soil_source": soil_source,
        "rainfall_erosivity_reference": {
            "value": rainfall_erosivity(latitude, longitude),
            "source": EROSIVITY_SOURCE_URL,
            "unit": "MJ mm/(ha h year)",
            "basis": "historical_grid_reference",
        },
    }
    calendar = load_thermal_reference(latitude, longitude)
    # One stated projection by default keeps acquisitions for a long sequence bounded.
    scenarios = (
        ["ssp245"]
        if is_strategy and "scenarios" not in request.model_fields_set
        else request.scenarios
    )
    weather = climate_weather.load_climate_weather(
        latitude, longitude, start, end, request.models, scenarios
    )
    return FieldInputs(details, layers, soil_source, calendar, weather, ruslan_dir)


def run_balance(inputs, planting_date, selected, candidates, *, strategy=False, irrigation=None):
    return climate_balance.compare_candidates(
        latitude=inputs.details["latitude"],
        longitude=inputs.details["longitude"],
        soil_layers=inputs.soil_layers,
        soil_source=inputs.soil_source,
        historical_weather=inputs.calendar,
        climate_weather=inputs.weather,
        candidates=[choice.engine_input() for choice in candidates],
        planting_date=planting_date,
        selected=[choice.engine_input() for choice in selected],
        ruslan_dir=inputs.ruslan_dir,
        strategy=strategy,
        irrigation_capacity_mm=irrigation.daily_mm if irrigation else None,
    )


def whole_median(values):
    values = list(values)
    return (
        round(median(values), 3) if values and all(value is not None for value in values) else None
    )


def common_amount(rows, key):
    return (
        round(fsum(row[key] for row in rows), 3)
        if rows and all(row[key] is not None for row in rows)
        else None
    )


def proposed_season(choice, date_, harvest=None):
    return AgronomySeason(
        season_year=date_.year,
        crop_id=choice.crop_id,
        planting_date=date_,
        harvest_date=harvest,
        harvest_date_basis="modeled_calendar_end" if harvest else None,
        hybrid=choice.hybrid,
        harvested_yield=choice.harvested_yield,
    )


def common_model_end(scenarios):
    ends = {str(scenario["end_date"]) for scenario in scenarios.values()}
    return date.fromisoformat(ends.pop()) if len(ends) == 1 else None


def finance(inputs, choice):
    return season_economics(
        choice.crop_id,
        choice.economics,
        country_code=inputs.details["country_code"],
        marsden=(inputs.details["latitude"], inputs.details["longitude"]) == MARSDEN_COORDINATES,
        region_code=inputs.details["agronomy_region_code"],
        seed_cost=choice.seed_cost,
    )


def water_stats(scenarios, irrigation=None):
    stats = {
        key: whole_median(value["metrics"].get(metric) for value in scenarios.values())
        for key, metric in (
            ("water_deficit_pct", "water_shortfall_pct"),
            ("water_deficit_mm", "deficit_mm"),
            ("drought_stress_days", "drought_stress_days"),
            ("water_stress_days_pct", "water_stress_days_pct"),
            ("living_cover_days", "crop_days"),
        )
    }
    if irrigation is None:
        return stats | {"scheduled_irrigation_mm": 0 if scenarios else None}
    return stats | {
        "water_demand_mm": whole_median(s["metrics"].get("demand_mm") for s in scenarios.values()),
        "scheduled_irrigation_mm": whole_median(
            s["metrics"].get("scheduled_irrigation_mm") for s in scenarios.values()
        )
        if irrigation.status == "none" or irrigation.daily_mm is not None
        else None,
    }


def candidate_result(inputs, choice, planting_date, raw, history, irrigation=None):
    scenarios = raw["scenarios"]
    end = common_model_end(scenarios)
    agronomy = assess_rotation_agronomy(
        history,
        candidate=proposed_season(choice, planting_date, end),
        region_code=inputs.details["agronomy_region_code"],
        country_code=inputs.details["country_code"],
        allow_same_year=irrigation is not None,
    )
    erosion = summarize_soil(PlanSoilInputs(erosion=choice.erosion), planting_date, planting_date)
    season_agronomy = agronomy.candidate
    rule = season_agronomy.disease_guidance
    previous_year = season_agronomy.previous_same_crop_season_year
    earliest_year = (
        previous_year + rule.minimum_return_interval_years
        if (previous_year is not None and rule and rule.minimum_return_interval_years is not None)
        else None
    )
    warnings = []
    if season_agronomy.disease_interval_status == "below_reference_interval":
        warnings.append("below_regional_crop_return_interval")
    if any(scenario["end_reason"] == "killing frost" for scenario in scenarios.values()):
        warnings.append("frost_ended_one_or_more_scenarios")
    return {key: value for key, value in raw.items() if key != "selected_scenarios"} | {
        "choice_id": choice.choice_id or choice.crop_id,
        "choice": choice.model_dump(mode="json", exclude_none=True),
        "water": water_stats(scenarios, irrigation),
        "economics": finance(inputs, choice),
        "agronomy": agronomy.model_dump(mode="json"),
        "same_crop_return_reference_year": earliest_year,
        "warnings": warnings,
        "erosion": {
            "t_ha_year": erosion.erosion_t_ha_year,
            "source": erosion.erosion_source,
            "status": "rusle_screening" if choice.erosion else "management_factors_unavailable",
        },
        "required_equipment": recommended_equipment([choice.crop_id], None),
    }


def rank(request, entries, key, irrigation=None):
    priority = request.farmer.priorities[0] if request.farmer.priorities else "water_deficit"
    settings = FarmerSettings(**request.farmer.model_dump(exclude={"priorities"}))
    equipment = rank_plans(
        settings,
        [
            PlanDecisionInput(id=row[key], required_equipment=row["required_equipment"])
            for row in entries
        ],
    )
    for row, possible in zip(entries, equipment.decisions, strict=True):
        row["feasibility"] = possible.feasibility
        row["blockers"] = possible.blockers
        row["equipment_to_confirm"] = possible.unknowns
        row["rank"] = None
    valid = [
        r
        for r in entries
        if r["status"] == "ok"
        and r["feasibility"] != "infeasible"
        and "below_regional_crop_return_interval" not in r.get("warnings", [])
    ]
    lower_is_better = priority in (
        "water_deficit",
        "drought_stress",
        "water_stress",
        "water_saving",
    )
    if priority in ("yield", "revenue", "revenue_after_seed"):
        field = {
            "yield": "marketable_yield_t_ha",
            "revenue": "gross_revenue_per_ha",
            "revenue_after_seed": "revenue_after_seed_per_ha",
        }[priority]

        def value(row):
            return row["economics"][field]
    else:
        field = {
            "water_deficit": "water_deficit_pct",
            "drought_stress": "drought_stress_days",
            "water_stress": "water_stress_days_pct",
            "water_saving": "scheduled_irrigation_mm",
            "soil_cover": "living_cover_days",
        }[priority]

        def value(row):
            return row["water"][field]

    is_strategy = isinstance(request, ClimateStepRequest) and request.strategy_mode is not None
    comparison = is_strategy and request.strategy_mode == "compare"
    comparable = len(valid) >= (2 if comparison else 1) and all(
        value(row) is not None for row in valid
    )
    if priority in ("revenue", "revenue_after_seed"):
        currencies = {row["economics"]["currency"] for row in valid}
        comparable = comparable and len(currencies) == 1 and None not in currencies
    if priority == "water_saving" and (irrigation is None or irrigation.daily_mm is None):
        comparable = False
    if comparable:
        valid.sort(key=value, reverse=not lower_is_better)
        last = None
        for index, row in enumerate(valid, 1):
            current = value(row)
            if index == 1 or current != last:
                rank_number = index
            row["rank"] = rank_number
            last = current
    limit = climate_balance.MAX_STRATEGY_CANDIDATES if is_strategy else 3
    return {
        "priority": priority,
        "status": "ranked" if comparable else "priority_unavailable",
        "rank_metric": field,
        "ranked_ids": [row[key] for row in valid] if comparable else [],
        "selection_ids": [row[key] for row in valid[:limit]],
        "selection_basis": "chosen_priority" if comparable else "eligible_input_order",
    }


def output_context(inputs, result):
    return {
        "field": inputs.details,
        "data_kind": "conditional_scenario",
        "weather_dataset": inputs.weather["dataset"],
        "weather_source_url": inputs.weather["dataset_url"],
        "weather_delivery": inputs.weather["delivery"],
        "weather_cases": result["weather_members"],
        "model_source": result["model_source"],
        "ruslan_balance_sha256": result["ruslan_balance_sha256"],
        "available_water_capacity_mm": result["available_water_capacity_mm"],
        "thermal_reference": result["historical_weather"],
        "assumptions": result["assumptions"],
        "field_validation_error": None,
        "water_stress_comparable": False,
        "fertilizer_recommendation_kg_ha": None,
        "modeled_yield_loss_probability_pct": None,
        "methods": {
            "water": (
                "Ruslan daily water balance; weather drives the modeled crop and soil water fluxes."
            ),
            "deficit": (
                "Sum of unmet crop water use in mm; percentage divides by "
                "seasonal potential crop water use."
            ),
            "drought_stress": (
                "Crop days below the modeled dry-stress threshold after establishment."
            ),
            "nutrients": (
                "Estimated harvested removal or measured harvest multiplied by "
                "compatible published crop coefficients."
            ),
        },
        "history_scope": (
            "Selected plans replay soil water. Observed history locally "
            "screens crop return and harvested nutrients; it does not "
            "initialize projected soil chemistry."
        ),
    }


def model_history(request, selected, selected_scenarios):
    history = list(request.observed_crop_history)
    for index, season in enumerate(selected):
        ends = {key: values[index] for key, values in selected_scenarios.items()}
        history.append(proposed_season(season, season.planting_date, common_model_end(ends)))
    return history


def step(request: ClimateStepRequest) -> dict:
    is_strategy = request.strategy_mode is not None
    irrigation = (request.irrigation or StrategyIrrigation()) if is_strategy else None
    choices = request.candidate_choices()
    inputs = resolve_inputs(request, request.planting_date, request.selected)
    result = run_balance(
        inputs,
        request.planting_date,
        request.selected,
        choices,
        strategy=is_strategy,
        irrigation=irrigation,
    )
    selected_cases = result["selected_scenarios"]
    history = model_history(request, request.selected, selected_cases)
    candidates = [
        candidate_result(inputs, choice, request.planting_date, value, history, irrigation)
        for choice, value in zip(choices, result["candidates"], strict=True)
    ]
    maximum_steps = (
        1
        if request.strategy_mode == "compare"
        else (climate_balance.MAX_STRATEGY_STEPS if is_strategy else 3)
    )
    if is_strategy:
        for candidate in candidates:
            if any(
                member["end_reason"] not in {"maturity", "harvest"}
                or (member["metrics"].get("demand_mm") or 0) <= 1e-9
                for member in candidate["scenarios"].values()
            ):
                candidate["status"] = "unsupported"
                candidate["reason"] = (
                    "The crop did not complete a viable thermal cycle in all members."
                )
            if candidate["choice"].get("hybrid") is None and (
                calendar := candidate.get("phenology", {}).get("calendar_id")
            ):
                candidate["choice"]["calendar_id"] = calendar
    ranking = rank(request, candidates, "choice_id", irrigation)
    output = output_context(inputs, result) | {
        "step": len(request.selected) + 1,
        "max_steps": maximum_steps,
        "planting_date": request.planting_date,
        "selected": request.selected,
        "selected_water_by_scenario": selected_cases,
        "has_next_step_after_selection": len(request.selected) + 1 < maximum_steps
        and (
            request.planting_date < date(2049, 7, 31)
            if is_strategy
            else request.planting_date.year < 2049
        ),
        "candidates": candidates,
        "ranking": ranking,
        "top_candidates": [
            next(row for row in candidates if row["choice_id"] == key)
            for key in (ranking["ranked_ids"] if is_strategy else ranking["selection_ids"])
        ],
    }
    if is_strategy:
        output.update(
            strategy_mode=request.strategy_mode,
            irrigation=irrigation.model_dump(mode="json"),
            eligible_count=len(ranking["selection_ids"]),
            observed_previous_crop=previous_observed_crop(request),
        )
        output["assumptions"]["irrigation"] = {
            "unknown": "Rainfed scenario only; irrigation availability is unknown.",
            "none": "No irrigation is available.",
            "available": (
                "Adaptive net water delivery within the daily capacity during growth."
                if irrigation.daily_mm is not None
                else "Rainfed scenario only; available irrigation volume is unknown."
            ),
        }[irrigation.status]
        if irrigation.status != "none" and irrigation.daily_mm is None:
            output["assumptions"]["irrigation_mm"] = None
    return output


def previous_observed_crop(request: ClimateStepRequest):
    previous = max(request.observed_crop_history, key=lambda row: row.season_year, default=None)
    if previous is None or previous.land_use != "crop":
        return None
    sowing_date = request.selected[0].planting_date if request.selected else request.planting_date
    return {
        "crop_id": previous.crop_id,
        "planting_date": previous.planting_date,
        "harvest_date": previous.harvest_date,
        "fallow_days_before_first_planting": (
            (sowing_date - previous.harvest_date).days - 1
            if previous.harvest_date and previous.harvest_date < sowing_date
            else None
        ),
    }


def aggregate_plan(scenarios, seasons):
    water = {}
    for case, rows in scenarios.items():
        metrics = [row["metrics"] for row in rows]
        demand = common_amount(metrics, "demand_mm")
        deficit = common_amount(metrics, "deficit_mm")
        water[case] = {
            "metrics": {
                "demand_mm": demand,
                "deficit_mm": deficit,
                "water_shortfall_pct": round(100 * deficit / demand, 2) if demand else None,
                "drought_stress_days": sum(row["drought_stress_days"] for row in metrics),
                "crop_days": sum(row["crop_days"] for row in metrics),
                "waterlogging_stress_days": None,
                "water_stress_days": None,
                "water_stress_days_pct": None,
            },
            "seasons": rows,
        }
    economics = [row["economics"] for row in seasons]
    currencies = {item["currency"] for item in economics}
    same_currency = len(currencies) == 1 and None not in currencies
    return water, {
        "currency": next(iter(currencies)) if same_currency else None,
        "marketable_yield_t_ha": common_amount(economics, "marketable_yield_t_ha"),
        "gross_revenue_per_ha": common_amount(economics, "gross_revenue_per_ha")
        if same_currency
        else None,
        "revenue_after_seed_per_ha": common_amount(economics, "revenue_after_seed_per_ha")
        if same_currency
        else None,
    }


def harvested_totals(seasons):
    rows = [season["agronomy"]["candidate"]["harvested_nutrients"] for season in seasons]
    complete = rows and all(row and row["status"] == "calculated" for row in rows)
    return {"status": "calculated" if complete else "compatible_field_yields_unavailable"} | {
        key: common_amount(rows, key) if complete else None
        for key in (
            "nitrogen_removed_kg_ha",
            "phosphorus_p2o5_removed_kg_ha",
            "potassium_k2o_removed_kg_ha",
        )
    }


def compare(request: ClimatePlansRequest) -> dict:
    first = request.plans[0].seasons
    inputs = resolve_inputs(request, first[-1].planting_date, first[:-1])
    plans = []
    for plan in request.plans:
        *previous, final = plan.seasons
        result = run_balance(inputs, final.planting_date, previous, [final])
        last = result["candidates"][0]
        previous_cases = result["selected_scenarios"]
        history = list(request.observed_crop_history)
        seasons = []
        for index, entry in enumerate(previous):
            cases = {key: values[index] for key, values in previous_cases.items()}
            seasons.append(
                candidate_result(
                    inputs,
                    entry,
                    entry.planting_date,
                    {
                        "crop_id": entry.crop_id,
                        "status": "ok" if cases else "unavailable",
                        "scenarios": cases,
                    },
                    history,
                )
            )
            history.append(proposed_season(entry, entry.planting_date, common_model_end(cases)))
        seasons.append(candidate_result(inputs, final, final.planting_date, last, history))
        cases = {key: [*previous_cases[key], value] for key, value in last["scenarios"].items()}
        pooled, economics = aggregate_plan(cases, seasons)
        plans.append(
            {
                "id": plan.id,
                "status": last["status"],
                "reason": last.get("reason"),
                "seasons": seasons,
                "scenarios": pooled,
                "water": water_stats(pooled),
                "economics": economics,
                "harvested_nutrients": harvested_totals(seasons),
                "warnings": sorted(
                    {warning for season in seasons for warning in season["warnings"]}
                ),
                "required_equipment": recommended_equipment(
                    [entry.crop_id for entry in plan.seasons], None
                ),
            }
        )
    return output_context(inputs, result) | {"plans": plans, "ranking": rank(request, plans, "id")}
