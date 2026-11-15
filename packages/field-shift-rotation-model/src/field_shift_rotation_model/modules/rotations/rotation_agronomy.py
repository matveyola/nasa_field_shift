"""Assess documented crop history, regional guidance and harvested nutrients."""

from collections.abc import Sequence
from datetime import date
from functools import lru_cache
from importlib.resources import files
from statistics import median
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from field_shift_rotation_model.modules.rotations.global_defaults import (
    country_benchmark,
    global_artifact,
)
from field_shift_rotation_model.modules.rotations.hybrid_profiles import StageLengths
from field_shift_rotation_model.modules.rotations.soil_indicators import Nonnegative, Source

FAO_DIVERSIFICATION_URL = (
    "https://www.fao.org/conservation-agriculture/overview/conservation-agriculture-principles/en"
)
FAO_COVER_URL = "https://www.fao.org/conservation-agriculture/in-practice/soil-organic-cover/en"

SeasonYear = Annotated[int, Field(ge=1, le=9999)]
LandUse = Literal["crop", "fallow", "unknown"]
HarvestDateBasis = Literal["supplied", "modeled_calendar_end"]
WebSource = Annotated[str, Field(pattern=r"^https?://\S+$")]

_CROP_GROUPS = {
    "wheat_spring": "wheat",
    "wheat_winter_frozen": "wheat",
    "wheat_winter_nonfrozen": "wheat",
}


class DocumentedHybrid(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Source
    source_url: WebSource
    relative_maturity_rating: Nonnegative | None = None
    rating_system: Source | None = None
    stage_lengths: StageLengths | None = None

    @model_validator(mode="after")
    def require_rating_context(self):
        if self.relative_maturity_rating is not None and self.rating_system is None:
            raise ValueError("A relative maturity rating requires its rating system.")
        return self


class HarvestedYield(BaseModel):
    model_config = ConfigDict(extra="forbid")

    yield_t_ha: Nonnegative
    basis: Source = Field(
        description="Set faostat_harvested_product only for a confirmed compatible basis."
    )
    source: Source


class AgronomySeason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    season_year: SeasonYear
    crop_id: Source | None = None
    planting_date: date | None = None
    harvest_date: date | None = None
    harvest_date_basis: HarvestDateBasis | None = None
    land_use: LandUse = "crop"
    hybrid: DocumentedHybrid | None = None
    harvested_yield: HarvestedYield | None = None

    @model_validator(mode="after")
    def validate_cultivation(self):
        crop_details = (
            self.crop_id,
            self.planting_date,
            self.harvest_date,
            self.harvest_date_basis,
            self.hybrid,
            self.harvested_yield,
        )
        if self.land_use == "crop" and self.crop_id is None:
            raise ValueError("A crop season requires crop_id.")
        if self.land_use != "crop" and any(value is not None for value in crop_details):
            raise ValueError("A fallow or unknown season cannot include crop details.")
        if (
            self.planting_date is not None
            and self.harvest_date is not None
            and self.harvest_date < self.planting_date
        ):
            raise ValueError("Harvest cannot precede planting.")
        if self.harvest_date_basis is not None and self.harvest_date is None:
            raise ValueError("A harvest date basis requires harvest_date.")
        return self


class ReturnGuidance(BaseModel):
    model_config = ConfigDict(frozen=True)

    crop_group: str
    scope_code: str
    scope_name: str
    topic: Literal["disease", "diversification"] = "disease"
    minimum_return_interval_years: int | None = None
    published_return_interval_years: tuple[int, int] | None = None
    note: str
    source_url: str
    related_source_urls: tuple[str, ...] = ()


class HybridReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    crop_id: str
    region_code: str
    trial_location: str
    trial_years: tuple[int, ...]
    name: str
    kind: Literal["hybrid", "cultivar"] = "hybrid"
    relative_maturity_rating: Nonnegative | None = None
    rating_system: str | None = None
    thermal_time_to_silking_c_days: float | None = None
    thermal_time_to_physiological_maturity_c_days: float | None = None
    trial_planting_date: date | None = None
    observed_days_after_planting_to_flowering: Nonnegative | None = None
    observed_days_after_planting_to_maturity: Nonnegative | None = None
    observed_flowering_duration_days: Nonnegative | None = None
    observed_yield_bu_ac: Nonnegative | None = None
    source_url: str
    rating_source_url: str | None = None
    rating_meaning_source_url: str | None = None

    @model_validator(mode="after")
    def require_rating_context(self):
        if self.relative_maturity_rating is not None and not (
            self.rating_system and self.rating_source_url
        ):
            raise ValueError("A relative maturity rating requires its system and source.")
        return self


class _ReferenceCatalog(BaseModel):
    return_guidance: list[ReturnGuidance]
    hybrid_references: list[HybridReference]


class HarvestedNutrients(BaseModel):
    method: Literal["faostat_coefficient_times_compatible_harvested_yield"] = (
        "faostat_coefficient_times_compatible_harvested_yield"
    )
    status: Literal["calculated", "missing_yield", "incompatible_yield_basis", "no_coefficient"]
    nitrogen_removed_kg_ha: float | None = None
    phosphorus_p2o5_removed_kg_ha: float | None = None
    potassium_k2o_removed_kg_ha: float | None = None
    nitrogen_kg_per_t: float | None = None
    phosphorus_p2o5_kg_per_t: float | None = None
    potassium_k2o_kg_per_t: float | None = None
    nitrogen_fixation_credit_kg_ha: None = None
    coefficient_source_url: str | None = None
    coefficient_source_row: str | None = None
    required_yield_basis: str | None = None
    harvested_yield: HarvestedYield | None = None


class CountryNutrientReference(BaseModel):
    country_code: str
    yield_years: tuple[int, ...]
    national_yield_t_ha: float
    source_url: str
    method: Literal["median_of_reported_faostat_national_yields"] = (
        "median_of_reported_faostat_national_yields"
    )
    nutrient_removal: HarvestedNutrients


class SeasonAgronomy(BaseModel):
    season_year: int
    crop_id: str | None
    land_use: LandUse
    planting_date: date | None
    harvest_date: date | None
    harvest_date_basis: HarvestDateBasis | None = None
    previous_same_crop_season_year: int | None = None
    same_crop_return_interval_years: int | None = None
    intervening_calendar_seasons: int | None = None
    previous_crop_season_year: int | None = None
    previous_crop_harvest_date: date | None = None
    previous_crop_harvest_date_basis: HarvestDateBasis | None = None
    days_between_harvest_and_planting: int | None = None
    harvest_to_planting_status: Literal[
        "calculated", "missing_dates", "incomplete_history", "overlap", "not_applicable"
    ] = "not_applicable"
    disease_guidance: ReturnGuidance | None = None
    rotation_note: ReturnGuidance | None = None
    minimum_intervening_seasons: int | None = None
    disease_interval_status: Literal[
        "below_reference_interval",
        "meets_reference_interval",
        "incomplete_history",
        "qualitative_guidance",
        "no_matching_guidance",
        "not_applicable",
    ] = "not_applicable"
    hybrid: DocumentedHybrid | None = None
    regional_hybrid_references: list[HybridReference] = Field(default_factory=list)
    regional_cultivar_references: list[HybridReference] = Field(default_factory=list)
    harvested_nutrients: HarvestedNutrients | None = None
    national_nutrient_reference: CountryNutrientReference | None = None


class AgronomyAssessment(BaseModel):
    history: list[SeasonAgronomy]
    candidate: SeasonAgronomy | None = None
    recorded_fallow_seasons: list[int]
    undocumented_seasons: list[int]
    minimum_fallow_seasons: None = None
    fallow_requirement_status: Literal["no_general_requirement"] = "no_general_requirement"
    fallow_guidance_sources: tuple[str, str] = (FAO_DIVERSIFICATION_URL, FAO_COVER_URL)
    scope_note: str = (
        "Disease references screen the same-crop interval only. Some pathogens also have "
        "other crop hosts. Seasonal gaps are distinct from recorded fallow. Nutrient removal "
        "refers to harvested product. National nutrient references use national benchmark "
        "yields and provide scale for planning. Fertilizer rates and nitrogen fixation "
        "credits are unavailable here."
    )


@lru_cache(maxsize=1)
def _reference_catalog() -> _ReferenceCatalog:
    resource = files(__package__).joinpath("artifacts").joinpath("rotation-agronomy-guidance.json")
    return _ReferenceCatalog.model_validate_json(resource.read_text(encoding="utf-8"))


def cultivar_references() -> tuple[HybridReference, ...]:
    return tuple(_reference_catalog().hybrid_references)


def _group(crop_id: str | None) -> str | None:
    return _CROP_GROUPS.get(crop_id, crop_id) if crop_id is not None else None


def _harvest_date_basis(season: AgronomySeason) -> HarvestDateBasis | None:
    if season.harvest_date is None:
        return None
    return season.harvest_date_basis or "supplied"


def _disease_guidance(
    crop_group: str | None, region_code: str | None, country_code: str | None
) -> ReturnGuidance | None:
    for scope_code in dict.fromkeys((region_code, country_code)):
        for row in _reference_catalog().return_guidance:
            if row.crop_group == crop_group and row.scope_code == scope_code:
                return row
    return None


def _interval_status(
    season_year: int,
    last_same_year: int | None,
    minimum_interval: int,
    earlier: Sequence[AgronomySeason],
) -> Literal["below_reference_interval", "meets_reference_interval", "incomplete_history"]:
    if last_same_year is not None and season_year - last_same_year < minimum_interval:
        return "below_reference_interval"
    known_years = {row.season_year for row in earlier if row.land_use != "unknown"}
    needed_years = range(season_year - minimum_interval + 1, season_year)
    if all(year in known_years for year in needed_years):
        return "meets_reference_interval"
    return "incomplete_history"


def _harvested_nutrients(
    crop_id: str | None, harvested_yield: HarvestedYield | None
) -> HarvestedNutrients:
    coefficient = global_artifact("nutrients")["crops"].get(crop_id)
    if coefficient is None:
        return HarvestedNutrients(status="no_coefficient", harvested_yield=harvested_yield)

    n_kg_per_t = coefficient["raw"].get("n_kg_per_t")
    p2o5_kg_per_t = coefficient["p2o5_kg_per_t"]
    k2o_kg_per_t = coefficient["k2o_kg_per_t"]
    basis = coefficient["yield_basis"]
    evidence = {
        "coefficient_source_url": coefficient["source_url"],
        "coefficient_source_row": coefficient["source_row"],
        "required_yield_basis": basis,
        "harvested_yield": harvested_yield,
        "nitrogen_kg_per_t": n_kg_per_t,
        "phosphorus_p2o5_kg_per_t": p2o5_kg_per_t,
        "potassium_k2o_kg_per_t": k2o_kg_per_t,
    }
    if harvested_yield is None:
        return HarvestedNutrients(status="missing_yield", **evidence)
    if harvested_yield.basis != basis:
        return HarvestedNutrients(status="incompatible_yield_basis", **evidence)

    tonnes = harvested_yield.yield_t_ha
    return HarvestedNutrients(
        status="calculated",
        nitrogen_removed_kg_ha=round(tonnes * n_kg_per_t, 3) if n_kg_per_t is not None else None,
        phosphorus_p2o5_removed_kg_ha=round(tonnes * p2o5_kg_per_t, 3),
        potassium_k2o_removed_kg_ha=round(tonnes * k2o_kg_per_t, 3),
        **evidence,
    )


def _national_nutrient_reference(
    crop_id: str | None, country_code: str | None, through_year: int
) -> CountryNutrientReference | None:
    if crop_id is None or country_code is None:
        return None
    benchmark = country_benchmark(country_code, crop_id)
    coefficient = global_artifact("nutrients")["crops"].get(crop_id)
    if benchmark is None or coefficient is None:
        return None
    raw = benchmark["raw"]
    if raw["item_code"] != coefficient["raw"]["item_code"]:
        return None

    observations = [
        row
        for row in raw.get("yield", ())
        if row["flag"] == "A" and row["unit"] == "kg/ha" and row["year"] <= through_year
    ]
    if not observations:
        return None
    tonnes = median(row["value"] for row in observations) / 1000
    source_url = global_artifact("economics")["source_urls"]["production"]["url"]
    return CountryNutrientReference(
        country_code=country_code,
        yield_years=tuple(row["year"] for row in observations),
        national_yield_t_ha=tonnes,
        source_url=source_url,
        nutrient_removal=_harvested_nutrients(
            crop_id,
            HarvestedYield(yield_t_ha=tonnes, basis=coefficient["yield_basis"], source=source_url),
        ),
    )


def _assess_season(
    season: AgronomySeason,
    earlier: Sequence[AgronomySeason],
    region_code: str | None,
    guidance_country_code: str | None,
    faostat_country_code: str | None,
) -> SeasonAgronomy:
    assessment = SeasonAgronomy(
        season_year=season.season_year,
        crop_id=season.crop_id,
        land_use=season.land_use,
        planting_date=season.planting_date,
        harvest_date=season.harvest_date,
        harvest_date_basis=_harvest_date_basis(season),
        hybrid=season.hybrid,
    )
    if season.land_use != "crop":
        return assessment

    group = _group(season.crop_id)
    previous_crops = [row for row in earlier if row.land_use == "crop"]
    previous = previous_crops[-1] if previous_crops else None
    last_same = next(
        (row for row in reversed(previous_crops) if _group(row.crop_id) == group), None
    )
    if last_same:
        assessment.previous_same_crop_season_year = last_same.season_year
        assessment.same_crop_return_interval_years = season.season_year - last_same.season_year
        assessment.intervening_calendar_seasons = assessment.same_crop_return_interval_years - 1
    if previous is not None:
        assessment.previous_crop_season_year = previous.season_year
        assessment.previous_crop_harvest_date = previous.harvest_date
        assessment.previous_crop_harvest_date_basis = _harvest_date_basis(previous)
        known_years = {row.season_year for row in earlier if row.land_use != "unknown"}
        intervening_years = range(previous.season_year + 1, season.season_year)
        if any(year not in known_years for year in intervening_years):
            assessment.harvest_to_planting_status = "incomplete_history"
        elif previous.harvest_date is None or season.planting_date is None:
            assessment.harvest_to_planting_status = "missing_dates"
        elif season.planting_date < previous.harvest_date:
            assessment.harvest_to_planting_status = "overlap"
        else:
            assessment.harvest_to_planting_status = "calculated"
            assessment.days_between_harvest_and_planting = (
                season.planting_date - previous.harvest_date
            ).days
    else:
        assessment.harvest_to_planting_status = "incomplete_history"

    guidance = _disease_guidance(group, region_code, guidance_country_code)
    assessment.disease_interval_status = "no_matching_guidance"
    if guidance is not None:
        if guidance.topic == "diversification":
            assessment.rotation_note = guidance
        else:
            assessment.disease_guidance = guidance
            minimum_interval = guidance.minimum_return_interval_years
            assessment.disease_interval_status = "qualitative_guidance"
            if minimum_interval is not None:
                assessment.minimum_intervening_seasons = minimum_interval - 1
                assessment.disease_interval_status = _interval_status(
                    season.season_year,
                    assessment.previous_same_crop_season_year,
                    minimum_interval,
                    earlier,
                )
    regional_references = [
        row
        for row in cultivar_references()
        if row.crop_id == season.crop_id and row.region_code == region_code
    ]
    assessment.regional_hybrid_references = [
        row for row in regional_references if row.kind == "hybrid"
    ]
    assessment.regional_cultivar_references = [
        row for row in regional_references if row.kind == "cultivar"
    ]
    assessment.harvested_nutrients = _harvested_nutrients(season.crop_id, season.harvested_yield)
    assessment.national_nutrient_reference = _national_nutrient_reference(
        season.crop_id, faostat_country_code, season.season_year
    )
    return assessment


def assess_rotation_agronomy(
    history: Sequence[AgronomySeason],
    *,
    candidate: AgronomySeason | None = None,
    region_code: str | None = None,
    country_code: str | None = None,
) -> AgronomyAssessment:
    """Assess one record per season, using only earlier records for comparisons."""
    ordered = sorted(history, key=lambda row: row.season_year)
    history_years = {row.season_year for row in ordered}
    if len(history_years) != len(ordered):
        raise ValueError("Crop history supports one record per season_year.")
    if candidate is not None and ordered and candidate.season_year <= ordered[-1].season_year:
        raise ValueError("The candidate must follow the selected history.")

    region_code = region_code.upper().strip() if region_code else None
    country_code = country_code.upper().strip() if country_code else None
    if country_code is None and region_code is not None:
        country_code = region_code.split("-", 1)[0]
    guidance_country_code = "US" if country_code == "USA" else country_code
    faostat_country_code = "USA" if country_code == "US" else country_code

    assessments = [
        _assess_season(
            row, ordered[:index], region_code, guidance_country_code, faostat_country_code
        )
        for index, row in enumerate(ordered)
    ]
    candidate_assessment = (
        _assess_season(candidate, ordered, region_code, guidance_country_code, faostat_country_code)
        if candidate is not None
        else None
    )
    first_year = ordered[0].season_year if ordered else None
    last_year = ordered[-1].season_year if ordered else None
    if candidate is not None and first_year is not None:
        last_year = candidate.season_year - 1
    missing_years = (
        set(range(first_year, last_year + 1)) - history_years
        if first_year is not None and last_year is not None
        else set()
    )
    return AgronomyAssessment(
        history=assessments,
        candidate=candidate_assessment,
        recorded_fallow_seasons=[row.season_year for row in ordered if row.land_use == "fallow"],
        undocumented_seasons=sorted(
            missing_years | {row.season_year for row in ordered if row.land_use == "unknown"}
        ),
    )
