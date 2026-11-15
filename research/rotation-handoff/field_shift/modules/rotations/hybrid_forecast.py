from dataclasses import dataclass
from datetime import date, timedelta
from math import fsum
from statistics import median
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from field_shift.modules.crops.forecast_phenology import (
    growing_degree_days,
    stage_coefficient,
)
from field_shift.modules.rotations.erosion_inputs import (
    LS_SOURCE_URL,
    ErodibilityProviderError,
    fetch_erodibility,
    uniform_slope_ls,
)
from field_shift.modules.rotations.equipment import recommended_equipment
from field_shift.modules.rotations.farmer_priorities import (
    FarmerDecision,
    FarmerSettings,
    Identifier,
    PlanDecisionInput,
    PlanEconomics,
    add_economics,
    rank_plans,
)
from field_shift.modules.rotations.global_country import country_at
from field_shift.modules.rotations.global_defaults import (
    EROSIVITY_SOURCE_URL,
    country_benchmark,
    global_economics,
    global_harvest,
    rainfall_erosivity,
)
from field_shift.modules.rotations.global_soil import (
    SoilProviderError,
    fetch_soil_profile,
)
from field_shift.modules.rotations.historical_fields import (
    FieldConfig,
    Fraction,
    Latitude,
    Longitude,
    SoilLayer,
    WeatherDay,
)
from field_shift.modules.rotations.hybrid_profiles import (
    ResolvedProfile,
    StageLengths,
    resolve_profile,
)
from field_shift.modules.rotations.hybrid_weather import (
    WeatherProviderError,
    WeatherSeries,
    fetch_history,
    fetch_live,
)
from field_shift.modules.rotations.marsden_defaults import (
    CropBenchmarkUse,
    applies_to,
    benchmark_economics,
    benchmark_erosion,
    benchmark_harvest,
    marsden_artifact,
)
from field_shift.modules.rotations.soil_indicators import (
    PlanSoilInputs,
    SoilSummary,
    summarize_soil,
)
from field_shift.modules.rotations.water_management import (
    WaterManagementSummary,
    summarize_water_management,
)
from field_shift.modules.rotations.waterlogging import estimate_waterlogging
from field_shift.modules.rotations.waterlogging_inputs import (
    SoilWaterloggingProfile,
    WaterloggingLayer,
    fetch_waterlogging_inputs,
)
from field_shift.modules.simulation.forecast_soil import soil_water_from_layers
from field_shift.modules.simulation.water_balance import fao56_days

DAY = timedelta(days=1)
RECENT_YEARS = 5
MAX_PLANNING_DAYS = 366 * 3


class ForecastField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,47}$")
    name: str = Field(min_length=1, max_length=120)
    latitude: Latitude
    longitude: Longitude
    location_source_url: str | None = None
    soil_source_url: str | None = Field(default=None, min_length=1)
    soil_layers: tuple[SoilLayer, ...] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def complete_soil_override(self):
        if (self.soil_source_url is None) != (self.soil_layers is None):
            raise ValueError(
                "Supply both soil_layers and soil_source_url, or omit both."
            )
        if self.soil_layers is not None:
            FieldConfig.model_validate(self.model_dump())
        return self

    def resolve(self) -> FieldConfig:
        values = self.model_dump()
        if self.soil_layers is None:
            profile = fetch_soil_profile(self.latitude, self.longitude)
            values.update(
                soil_layers=profile.layers, soil_source_url=profile.source_url
            )
        return FieldConfig.model_validate(values)


class OpeningMoisture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theta: tuple[Fraction, Fraction, Fraction]
    source: str = Field(min_length=1, max_length=200)


class Planting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    planting_date: date
    through_date: date | None = None
    initial_moisture: OpeningMoisture | None = None

    @model_validator(mode="after")
    def check_interval(self):
        if self.through_date and self.through_date < self.planting_date:
            raise ValueError("through_date must be on or after planting_date.")
        return self


class HydraulicLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_m: FiniteFloat = Field(ge=0)
    bottom_m: FiniteFloat = Field(gt=0)
    saturation: Fraction = Field(gt=0)
    saturated_conductivity_mm_day: FiniteFloat = Field(ge=0)


class HydraulicSoil(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layers: tuple[HydraulicLayer, ...] = Field(min_length=1, max_length=20)
    source: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def contiguous_depth(self):
        top = 0.0
        for layer in self.layers:
            if abs(layer.top_m - top) > 1e-6 or layer.bottom_m <= layer.top_m:
                raise ValueError("Hydraulic layers must be ordered and contiguous.")
            top = layer.bottom_m
        if top < 1:
            raise ValueError("Hydraulic layers must cover at least the first metre.")
        return self


class CropChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    crop_id: str = Field(min_length=1, max_length=48)
    calendar_id: str | None = Field(default=None, max_length=48)
    stage_lengths: StageLengths | None = None
    economics: PlanEconomics | None = None

    @model_validator(mode="after")
    def choose_calendar(self):
        if self.calendar_id and self.stage_lengths:
            raise ValueError("Use either calendar_id or stage_lengths.")
        if self.stage_lengths and sum(self.stage_lengths) > 366:
            raise ValueError("The four stages must total at most 366 days.")
        return self


class IrrigationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    net_mm: FiniteFloat = Field(ge=0.0001)


class PlanChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,47}$")
    crops: list[CropChoice] = Field(min_length=1, max_length=3)
    irrigation: list[IrrigationEvent] = Field(default_factory=list, max_length=1098)
    required_equipment: list[Identifier] | None = Field(default=None, max_length=40)
    soil: PlanSoilInputs | None = None

    @model_validator(mode="after")
    def unique_irrigation_dates(self):
        if len({event.date for event in self.irrigation}) != len(self.irrigation):
            raise ValueError(
                "Combine irrigation on the same date into one net_mm entry."
            )
        return self


class ForecastRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,47}$")
    field: ForecastField | None = None
    country_code: str | None = Field(
        default=None,
        pattern=r"^[A-Z]{3}$",
        description="Optional ISO alpha-3 statistical area override.",
    )
    seasons: list[Planting] = Field(min_length=1, max_length=3)
    plans: list[PlanChoice] = Field(min_length=2, max_length=3)
    live_days: int = Field(default=7, ge=1, le=10)
    farmer: FarmerSettings | None = None
    estimate_waterlogging: bool = Field(
        default=False, description="Screen root-zone wetness using extra SoilGrids data."
    )
    waterlogging_soil: HydraulicSoil | None = None
    estimate_erosion_erodibility: bool = Field(
        default=False, description="Estimate metric soil erodibility K from SoilGrids."
    )

    @model_validator(mode="after")
    def consistent_plans(self):
        if (self.field_id is None) == (self.field is None):
            raise ValueError("Supply exactly one of field_id and field.")
        if len({plan.id for plan in self.plans}) != len(self.plans):
            raise ValueError("Plan IDs must be unique.")
        if any(len(plan.crops) != len(self.seasons) for plan in self.plans):
            raise ValueError("Each plan must supply one crop for each season.")
        dates = [season.planting_date for season in self.seasons]
        if dates != sorted(set(dates)):
            raise ValueError("Planting dates must be unique and in increasing order.")
        return self


class DateRange(BaseModel):
    start: date
    end: date


class SourceSegment(BaseModel):
    source: Literal["live_forecast", "historical_analog"]
    dates: DateRange
    historical_dates: DateRange | None = None


class WeatherProvenance(BaseModel):
    live_source_url: str | None
    historical_source_url: str | None
    historical_anchor_dates: list[date]
    member_count: int
    interpretation: Literal["live_only", "conditional_historical_scenarios"]


class SeasonResult(BaseModel):
    crop_id: str
    planting_date: date
    through_date: date
    calendar_id: str | None
    stage_lengths: tuple[int, int, int, int]
    root_depth_m: float
    root_depth_limited: bool
    capacity_mm: float
    opening_reserve_mm: float
    opening_source: str
    opening_historical_date: date | None
    weather_segments: list[SourceSegment]
    demand_mm: float
    modeled_use_mm: float
    deficit_mm: float
    ending_reserve_mm: float
    water_shortfall_pct: float | None
    crop_days: int
    drought_stress_days: int
    water_stress_days: int | None
    water_management: WaterManagementSummary
    flowering_start: date | None
    flowering_end: date | None
    flowering_stress_days: int | None


class ForecastMember(BaseModel):
    historical_anchor_date: date | None
    water_shortfall_pct: float | None
    crop_days: int
    drought_stress_days: int
    waterlogging_stress_days: int | None
    water_stress_days: int | None
    water_stress_days_pct: float | None
    seasons: list[SeasonResult]


class EconomicsSummary(BaseModel):
    marketable_yield_t_ha: float | None
    gross_revenue_per_ha: float | None = None
    gross_revenue_currency: str | None = None
    gross_margin_per_ha: float | None
    currency: str | None
    inputs: list[PlanEconomics | None]
    input_origins: list[Literal["supplied", "published_benchmark", "unavailable"]]


class PlanForecast(BaseModel):
    id: str
    minimum_pct: float | None
    median_pct: float | None
    maximum_pct: float | None
    median_water_stress_days_pct: float | None
    soil: SoilSummary
    erosion_inputs_missing: list[Literal["r", "k", "ls", "c", "p"]]
    soil_erodibility_k: float | None = None
    erosion_slope_ls: float | None = None
    erosion_site_source: str | None = None
    economics: EconomicsSummary
    crop_benchmarks: list[CropBenchmarkUse]
    required_equipment: list[Identifier] | None
    equipment_requirements_source: Literal["supplied", "catalog_baseline"] | None
    scheduled_net_irrigation_mm: float
    members: list[ForecastMember]


class ForecastResult(BaseModel):
    as_of: date
    field_id: str
    latitude: float
    longitude: float
    soil_source_url: str
    water_shortfall_method: Literal["formula"] = "formula"
    accuracy: Literal["not_validated_for_field"] = "not_validated_for_field"
    validation_error_pct_points: None = None
    water_stress_comparable: bool
    weather: WeatherProvenance
    field_references_url: str | None = None
    country_code: str | None = None
    country_code_source: Literal["supplied", "natural_earth"] | None = None
    rainfall_erosivity_r: float | None = None
    rainfall_erosivity_source_url: str | None = None
    waterlogging_soil_status: Literal[
        "not_requested", "estimated_from_soilgrids", "supplied", "soil_data_unavailable"
    ] = "not_requested"
    country_references_url: str | None = None
    plans: list[PlanForecast]
    decision: FarmerDecision | None = None


@dataclass(frozen=True)
class ScheduledCrop:
    planting: Planting
    crop_id: str
    profile: ResolvedProfile
    last_day: date

    @property
    def first_day(self) -> date:
        return self.planting.planting_date


def schedule_plans(
    request: ForecastRequest, as_of: date
) -> dict[str, list[ScheduledCrop]]:
    first = request.seasons[0].planting_date
    last_allowed = as_of + timedelta(days=MAX_PLANNING_DAYS)
    if first < as_of or request.seasons[-1].planting_date > last_allowed:
        raise ValueError(
            "Planting dates must be from today through the next three years."
        )
    schedules = {}
    for plan in request.plans:
        schedule = []
        for planting, crop in zip(request.seasons, plan.crops, strict=True):
            profile = resolve_profile(
                crop.crop_id,
                calendar_id=crop.calendar_id,
                stage_lengths=crop.stage_lengths,
            )
            last = planting.planting_date + timedelta(
                days=sum(profile.stage_lengths) - 1
            )
            if planting.through_date:
                last = min(last, planting.through_date)
            if last > last_allowed:
                raise ValueError(
                    "The calculation must finish within the next three years."
                )
            if schedule and schedule[-1].last_day >= planting.planting_date:
                raise ValueError(f"Crop seasons overlap in plan {plan.id}.")
            schedule.append(ScheduledCrop(planting, crop.crop_id, profile, last))
        for event in plan.irrigation:
            if not any(
                crop.first_day <= event.date <= crop.last_day for crop in schedule
            ):
                raise ValueError(
                    f"Irrigation on {event.date} is outside crop seasons in {plan.id}."
                )
        schedules[plan.id] = schedule
    return schedules


def history_anchors(first: date, last: date, as_of: date) -> list[date]:
    last_archive_day = date(as_of.year - 1, 12, 31)
    duration = last - first
    # Keep the most recent anchors whose entire sequence predates this UTC year.
    # A February 29 anchor uses only actual leap-day records.
    anchors = []
    for year in range(last_archive_day.year, last_archive_day.year - 12, -1):
        try:
            anchor = first.replace(year=year)
        except ValueError:
            continue
        if anchor + duration <= last_archive_day:
            anchors.append(anchor)
        if len(anchors) == RECENT_YEARS or (
            first.month == 2 and first.day == 29 and len(anchors) == 3
        ):
            break
    if not anchors:
        raise WeatherProviderError("No complete historical sequence is available.")
    return sorted(anchors)


def weather_for_crop(
    crop: ScheduledCrop,
    live: WeatherSeries | None,
    archive: WeatherSeries | None,
    anchor: date | None,
    first: date,
) -> tuple[list[WeatherDay], list[SourceSegment]]:
    days = []
    live_count = 0
    for index in range((crop.last_day - crop.first_day).days + 1):
        when = crop.first_day + index * DAY
        if live and when in live.days:
            days.append(live.days[when])
            live_count += 1
        elif archive and anchor:
            historical = anchor + (when - first)
            if historical not in archive.days:
                raise WeatherProviderError(f"Missing archive weather for {historical}.")
            days.append(archive.days[historical].model_copy(update={"date": when}))
        else:
            raise WeatherProviderError(f"Missing weather for {when}.")
    segments = []
    if live_count:
        segments.append(
            SourceSegment(
                source="live_forecast",
                dates=DateRange(
                    start=crop.first_day, end=crop.first_day + (live_count - 1) * DAY
                ),
            )
        )
    if anchor and live_count < len(days):
        start = crop.first_day + live_count * DAY
        segments.append(
            SourceSegment(
                source="historical_analog",
                dates=DateRange(start=start, end=crop.last_day),
                historical_dates=DateRange(
                    start=anchor + (start - first), end=anchor + (crop.last_day - first)
                ),
            )
        )
    return days, segments


def run_crop(
    crop: ScheduledCrop,
    layers: tuple[tuple[float, float, float, float], ...],
    live: WeatherSeries | None,
    archive: WeatherSeries | None,
    anchor: date | None,
    first: date,
    irrigation: dict[date, float],
    waterlogging_soil: SoilWaterloggingProfile | None,
) -> tuple[SeasonResult, float, float]:
    profile = crop.profile
    weather, segments = weather_for_crop(crop, live, archive, anchor, first)
    opening = crop.planting.initial_moisture
    opening_day = None
    if opening:
        moisture, opening_source = opening.theta, opening.source
    else:
        if archive is None or anchor is None:
            raise WeatherProviderError("An opening moisture value is required.")
        opening_day = anchor + (crop.first_day - first) - DAY
        moisture = archive.initial_theta.get(opening_day)
        if moisture is None:
            raise WeatherProviderError(
                f"Missing archived opening moisture for {opening_day}."
            )
        opening_source = "era5_land_historical_analog"
    soil = soil_water_from_layers(moisture, profile.root_depth_m, layers)
    threshold = (1 - profile.depletion_fraction) * soil.capacity_mm
    coefficients = {
        day.date: stage_coefficient(index, profile.stage_lengths, profile.coefficients)
        for index, day in enumerate(weather, 1)
    }
    balance = fao56_days(
        weather,
        initial_mm=soil.initial_reserve_mm,
        capacity_mm=soil.capacity_mm,
        threshold_mm=threshold,
        coefficient=profile.coefficients[1],
        daily_coefficients=coefficients,
        daily_irrigation_mm=irrigation,
    )
    water_management = summarize_water_management(balance)
    wet_stress_dates = None
    if waterlogging_soil is not None:
        wet = estimate_waterlogging(
            crop_id=crop.crop_id,
            weather=weather,
            coefficients=coefficients,
            irrigation=irrigation,
            moisture=moisture,
            depth_m=profile.root_depth_m,
            field_capacity=soil.field_capacity,
            wilting_point=soil.wilting_point,
            depletion_fraction=profile.depletion_fraction,
            soil=waterlogging_soil,
        )
        if wet is not None:
            estimate, wet_stress_dates = wet
            water_management.waterlogging = estimate
            water_management.waterlogging_stress_days = estimate.estimated_crop_stress_days
        else:
            water_management.waterlogging_profile_incompatible = True
    demand = sum(day.reference_et0_mm * coefficients[day.date] for day in weather)
    use = sum(day.water_use_mm for day in balance)
    deficit = max(0.0, demand - use)
    dry_stress_dates = {day.date for day in balance if day.reserve_mm < threshold}
    flowers = []
    if (
        profile.flowering_gdd
        and profile.base_c is not None
        and profile.upper_c is not None
    ):
        heat = 0.0
        for day, water in zip(weather, balance, strict=True):
            heat += growing_degree_days(
                day.low_c, day.high_c, profile.base_c, profile.upper_c
            )
            if profile.flowering_gdd[0] < heat <= profile.flowering_gdd[1]:
                flowers.append(water)
    return (
        SeasonResult(
            crop_id=crop.crop_id,
            planting_date=crop.first_day,
            through_date=crop.last_day,
            calendar_id=profile.calendar_id,
            stage_lengths=profile.stage_lengths,
            root_depth_m=profile.root_depth_m,
            root_depth_limited=profile.root_depth_limited,
            capacity_mm=soil.capacity_mm,
            opening_reserve_mm=soil.initial_reserve_mm,
            opening_source=opening_source,
            opening_historical_date=opening_day,
            weather_segments=segments,
            demand_mm=round(demand, 3),
            modeled_use_mm=round(use, 3),
            deficit_mm=round(deficit, 3),
            ending_reserve_mm=balance[-1].reserve_mm,
            water_shortfall_pct=round(100 * deficit / demand, 1) if demand else None,
            crop_days=len(balance),
            drought_stress_days=len(dry_stress_dates),
            water_stress_days=(
                len(dry_stress_dates | wet_stress_dates)
                if wet_stress_dates is not None
                else None
            ),
            water_management=water_management,
            flowering_start=flowers[0].date if flowers else None,
            flowering_end=flowers[-1].date if flowers else None,
            flowering_stress_days=(
                sum(day.date in dry_stress_dates for day in flowers) if flowers else None
            ),
        ),
        demand,
        deficit,
    )


def plan_benchmarks(
    plan: PlanChoice,
    schedule: list[ScheduledCrop],
    *,
    use_marsden: bool,
    country_code: str | None,
    erosion_factors: dict,
) -> tuple[EconomicsSummary, list[CropBenchmarkUse], PlanSoilInputs]:
    soil = plan.soil or PlanSoilInputs()
    inputs = []
    benchmarks = []
    harvests = []
    complete = all(
        (crop.last_day - crop.first_day).days + 1 == sum(crop.profile.stage_lengths)
        for crop in schedule
    )
    for choice, crop in zip(plan.crops, schedule, strict=True):
        uses_irrigation = any(
            crop.first_day <= event.date <= crop.last_day for event in plan.irrigation
        )
        economics = choice.economics
        defaulted = economics is None
        fao_row = None
        if defaulted and use_marsden:
            economics = benchmark_economics(
                choice.crop_id, uses_irrigation=uses_irrigation
            )
        elif defaulted and country_code:
            fao_row = country_benchmark(country_code, choice.crop_id)
            if fao_row:
                economics = global_economics(fao_row)
        inputs.append(economics)
        harvest = None
        if complete and soil.harvests is None and economics:
            if use_marsden:
                harvest = benchmark_harvest(choice.crop_id, economics)
            elif fao_row:
                harvest = global_harvest(choice.crop_id, economics)
        harvests.append(harvest)
        benchmarks.append(
            CropBenchmarkUse(
                crop_id=choice.crop_id,
                economics_source=(
                    "unavailable"
                    if economics is None
                    else "published_benchmark"
                    if defaulted
                    else "supplied"
                ),
                nutrient_source=harvest.source if harvest else None,
                nutrient_yield_t_ha=round(harvest.yield_t_ha, 6) if harvest else None,
                nutrient_yield_moisture_pct=(
                    marsden_artifact("nutrients")["crops"][choice.crop_id][
                        "yield_moisture_pct"
                    ]
                    if harvest and use_marsden
                    else None
                ),
                p2o5_kg_per_t=harvest.p2o5_kg_per_t if harvest else None,
                k2o_kg_per_t=harvest.k2o_kg_per_t if harvest else None,
                p2o5_removed_kg_ha=(
                    round(harvest.yield_t_ha * harvest.p2o5_kg_per_t, 3)
                    if harvest
                    else None
                ),
                k2o_removed_kg_ha=(
                    round(harvest.yield_t_ha * harvest.k2o_kg_per_t, 3)
                    if harvest
                    else None
                ),
            )
        )
    if soil.harvests is None and all(harvest is not None for harvest in harvests):
        soil = soil.model_copy(update={"harvests": harvests})
    if soil.erosion_management is not None:
        management = soil.erosion_management
        factors = benchmark_erosion(
            erosion_factors,
            c=management.c,
            p=management.p,
            source=management.source,
        )
        if factors is not None:
            soil = soil.model_copy(update={"erosion": factors})
    yield_t_ha, margin, currency = (
        add_economics(inputs) if complete else (None, None, None)
    )
    revenue = None
    revenue_currency = None
    priced = [
        (item.marketable_yield_t_ha * item.farmgate_price_per_t, item.currency)
        for item in inputs
        if item
        and item.marketable_yield_t_ha is not None
        and item.farmgate_price_per_t is not None
        and item.currency is not None
    ]
    if (
        complete
        and len(priced) == len(inputs)
        and len({code for _, code in priced}) == 1
    ):
        revenue = round(fsum(value for value, _ in priced), 2)
        revenue_currency = priced[0][1]
    return (
        EconomicsSummary(
            marketable_yield_t_ha=yield_t_ha,
            gross_revenue_per_ha=revenue,
            gross_revenue_currency=revenue_currency,
            gross_margin_per_ha=margin,
            currency=currency,
            inputs=inputs,
            input_origins=[row.economics_source for row in benchmarks],
        ),
        benchmarks,
        soil,
    )


def forecast(
    request: ForecastRequest, field: FieldConfig | ForecastField, *, as_of: date
) -> ForecastResult:
    schedules = schedule_plans(request, as_of)
    if isinstance(field, ForecastField):
        field = field.resolve()
    waterlogging_soil = None
    waterlogging_status = "not_requested"
    if request.waterlogging_soil is not None:
        supplied = request.waterlogging_soil
        waterlogging_soil = SoilWaterloggingProfile(
            layers=tuple(
                WaterloggingLayer(**layer.model_dump()) for layer in supplied.layers
            ),
            source_urls=(supplied.source,),
            method="supplied_hydraulic_profile",
        )
        waterlogging_status = "supplied"
    elif request.estimate_waterlogging or (
        request.farmer is not None and "water_stress" in request.farmer.priorities
    ):
        try:
            waterlogging_soil = fetch_waterlogging_inputs(field.latitude, field.longitude)
            waterlogging_status = "estimated_from_soilgrids"
        except SoilProviderError:
            waterlogging_status = "soil_data_unavailable"
    first = request.seasons[0].planting_date
    last = max(schedule[-1].last_day for schedule in schedules.values())
    live_through = as_of + (request.live_days - 1) * DAY
    live = (
        fetch_live(field.latitude, field.longitude, as_of=as_of, days=request.live_days)
        if first <= live_through
        else None
    )
    need_archive = last > live_through or any(
        season.initial_moisture is None for season in request.seasons
    )
    anchors = history_anchors(first, last, as_of) if need_archive else []
    archive = (
        fetch_history(
            field.latitude,
            field.longitude,
            anchors[0] - DAY,
            anchors[-1] + (last - first),
        )
        if anchors
        else None
    )
    layers = tuple(
        (layer.top_m, layer.bottom_m, layer.field_capacity, layer.wilting_point)
        for layer in field.soil_layers
    )
    plans = []
    decision_inputs = []
    use_marsden = applies_to(field) and request.country_code in (None, "USA")
    country_code = request.country_code or country_at(field.latitude, field.longitude)
    shared_factors = (
        marsden_artifact("erosion").get("shared_factors") or {}
        if use_marsden
        else {
            "r": rainfall_erosivity(field.latitude, field.longitude),
            "source": EROSIVITY_SOURCE_URL,
        }
    )
    if request.estimate_erosion_erodibility and shared_factors.get("k") is None:
        try:
            erodibility = fetch_erodibility(field.latitude, field.longitude)
        except ErodibilityProviderError:
            pass
        else:
            shared_factors["k"] = erodibility.k
            shared_factors["source"] += f"; {erodibility.source}"
    for plan in request.plans:
        schedule = schedules[plan.id]
        irrigation = {event.date: event.net_mm for event in plan.irrigation}
        members = []
        for anchor in anchors or [None]:
            seasons, demands, deficits = zip(
                *(
                    run_crop(
                        crop,
                        layers,
                        live,
                        archive,
                        anchor,
                        first,
                        irrigation,
                        waterlogging_soil,
                    )
                    for crop in schedule
                ),
                strict=True,
            )
            demand = sum(demands)
            crop_days = sum(season.crop_days for season in seasons)
            wet_days = [
                season.water_management.waterlogging_stress_days for season in seasons
            ]
            stress_days = [season.water_stress_days for season in seasons]
            combined_days = (
                sum(days for days in stress_days if days is not None)
                if all(days is not None for days in stress_days)
                else None
            )
            members.append(
                ForecastMember(
                    historical_anchor_date=anchor,
                    water_shortfall_pct=round(100 * sum(deficits) / demand, 1)
                    if demand
                    else None,
                    crop_days=crop_days,
                    drought_stress_days=sum(
                        season.drought_stress_days for season in seasons
                    ),
                    waterlogging_stress_days=(
                        sum(days for days in wet_days if days is not None)
                        if combined_days is not None
                        else None
                    ),
                    water_stress_days=combined_days,
                    water_stress_days_pct=(
                        round(100 * combined_days / crop_days, 1)
                        if combined_days is not None
                        else None
                    ),
                    seasons=list(seasons),
                )
            )
        values = [
            member.water_shortfall_pct
            for member in members
            if member.water_shortfall_pct is not None
        ]
        stress_values = [
            member.water_stress_days_pct
            for member in members
            if member.water_stress_days_pct is not None
        ]
        erosion_factors = dict(shared_factors)
        if plan.soil and plan.soil.erosion_management:
            management = plan.soil.erosion_management
            erosion_factors.update(
                {
                    key: value
                    for key, value in (("k", management.k), ("ls", management.ls))
                    if value is not None
                }
            )
            if management.slope_percent is not None:
                erosion_factors["ls"] = uniform_slope_ls(
                    management.slope_percent, management.slope_length_m
                )
                erosion_factors["source"] += f"; LS: {LS_SOURCE_URL}"
        economics, benchmarks, soil_inputs = plan_benchmarks(
            plan,
            schedule,
            use_marsden=use_marsden,
            country_code=country_code,
            erosion_factors=erosion_factors,
        )
        soil = summarize_soil(soil_inputs, first, last)
        equipment = plan.required_equipment
        equipment_source = "supplied" if equipment is not None else None
        if (
            equipment is None
            and request.farmer
            and request.farmer.equipment_ids is not None
        ):
            equipment = recommended_equipment(
                [crop.crop_id for crop in plan.crops],
                request.farmer.irrigation_system if irrigation else None,
            )
            equipment_source = "catalog_baseline"
        erosion_inputs_missing = []
        if soil_inputs.erosion is None:
            erosion_inputs_missing = [
                factor
                for factor in ("r", "k", "ls")
                if erosion_factors.get(factor) is None
            ]
            if soil_inputs.erosion_management is None:
                erosion_inputs_missing.extend(("c", "p"))
        computed = PlanForecast(
            id=plan.id,
            minimum_pct=min(values) if values else None,
            median_pct=round(median(values), 1) if values else None,
            maximum_pct=max(values) if values else None,
            median_water_stress_days_pct=(
                round(median(stress_values), 1)
                if len(stress_values) == len(members)
                else None
            ),
            soil=soil,
            erosion_inputs_missing=erosion_inputs_missing,
            soil_erodibility_k=(
                soil_inputs.erosion.k
                if soil_inputs.erosion is not None
                else erosion_factors.get("k")
            ),
            erosion_slope_ls=(
                soil_inputs.erosion.ls
                if soil_inputs.erosion is not None
                else erosion_factors.get("ls")
            ),
            erosion_site_source=(
                soil_inputs.erosion.source
                if soil_inputs.erosion is not None
                else erosion_factors.get("source")
            ),
            economics=economics,
            crop_benchmarks=benchmarks,
            required_equipment=equipment,
            equipment_requirements_source=equipment_source,
            scheduled_net_irrigation_mm=round(
                fsum(round(mm, 4) for mm in irrigation.values()), 4
            ),
            members=members,
        )
        plans.append(computed)
        decision_inputs.append(
            PlanDecisionInput(
                id=plan.id,
                required_equipment=equipment,
                uses_irrigation=bool(irrigation),
                median_water_stress_days_pct=computed.median_water_stress_days_pct,
                median_water_deficit_pct=computed.median_pct,
                irrigation_mm=computed.scheduled_net_irrigation_mm,
                marketable_yield_t_ha=economics.marketable_yield_t_ha,
                gross_revenue_per_ha=economics.gross_revenue_per_ha,
                gross_revenue_currency=economics.gross_revenue_currency,
                living_cover_days=soil.living_cover_days,
            )
        )
    return ForecastResult(
        as_of=as_of,
        field_id=field.id,
        latitude=field.latitude,
        longitude=field.longitude,
        soil_source_url=field.soil_source_url,
        water_stress_comparable=all(
            plan.median_water_stress_days_pct is not None for plan in plans
        ),
        waterlogging_soil_status=waterlogging_status,
        weather=WeatherProvenance(
            live_source_url=live.source_url if live else None,
            historical_source_url=archive.source_url if archive else None,
            historical_anchor_dates=anchors,
            member_count=len(anchors) or 1,
            interpretation="conditional_historical_scenarios"
            if archive
            else "live_only",
        ),
        field_references_url="/api/rotations/forecast/references/marsden_demo"
        if use_marsden
        else None,
        country_code=country_code,
        country_code_source=("supplied" if request.country_code else "natural_earth")
        if country_code
        else None,
        country_references_url=f"/api/rotations/forecast/countries/{country_code}"
        if country_code
        else None,
        rainfall_erosivity_r=shared_factors.get("r"),
        rainfall_erosivity_source_url=EROSIVITY_SOURCE_URL
        if shared_factors.get("r") is not None
        else None,
        plans=plans,
        decision=rank_plans(request.farmer, decision_inputs)
        if request.farmer
        else None,
    )
