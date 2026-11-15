from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from json import loads
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

FAO_COEFFICIENTS_URL = "https://www.fao.org/4/X0490E/x0490e0b.htm"
FAO_ROOTS_URL = "https://www.fao.org/4/X0490E/x0490e0e.htm"
MAX_ROOT_DEPTH_M = 1.0

StageLength = Annotated[int, Field(strict=True, gt=0)]
StageLengths = tuple[StageLength, StageLength, StageLength, StageLength]
_STAGE_LENGTHS = TypeAdapter(StageLengths)


class CalendarOption(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    stage_lengths: StageLengths
    reference_planting_months: str
    reference_region: str | None


class FloweringParameters(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_c: float
    upper_c: float
    flowering_gdd: tuple[int, int]
    source_url: str


class ForecastCrop(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    coefficients: tuple[float, float, float]
    mid_coefficient_range: tuple[float, float]
    end_coefficient_range: tuple[float, float]
    depletion_fraction: float
    root_depth_m: Annotated[float, Field(gt=0, le=MAX_ROOT_DEPTH_M)]
    root_depth_range_m: tuple[float, float]
    root_depth_limited: bool
    default_calendar_id: str | None
    calendars: tuple[CalendarOption, ...]
    flowering: FloweringParameters | None = None
    yield_factor: float | None = None
    coefficient_source_url: str = FAO_COEFFICIENTS_URL
    root_depth_source_url: str = FAO_ROOTS_URL


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    root_depth_m: float
    root_depth_limited: bool
    stage_lengths: StageLengths
    coefficients: tuple[float, float, float]
    depletion_fraction: float
    base_c: float | None
    upper_c: float | None
    flowering_gdd: tuple[int, int] | None
    yield_factor: float | None
    calendar_id: str | None


@lru_cache(maxsize=1)
def _load_catalog() -> dict[str, ForecastCrop]:
    resource = files(__package__).joinpath("artifacts").joinpath("forecast-crops.json")
    profiles = TypeAdapter(list[ForecastCrop]).validate_json(resource.read_text(encoding="utf-8"))
    return {profile.id: profile for profile in profiles}


def crop_catalog() -> list[ForecastCrop]:
    return list(_load_catalog().values())


@lru_cache(maxsize=1)
def strategy_hybrids() -> dict:
    resource = files(__package__).joinpath("artifacts", "strategy-hybrids.json")
    return {
        choice_id: {
            **{key: value for key, value in source.items() if key != "hybrids"},
            "name": name,
            "flowering_gdd": flowering,
            "maturity_gdd": maturity,
        }
        for source in loads(resource.read_text(encoding="utf-8")).values()
        for choice_id, (name, flowering, maturity) in source["hybrids"].items()
    }


def resolve_profile(
    crop_id: str,
    *,
    calendar_id: str | None = None,
    stage_lengths: StageLengths | None = None,
) -> ResolvedProfile:
    profile = _load_catalog().get(crop_id)
    if profile is None:
        raise ValueError(f"Unknown crop profile {crop_id!r}.")
    if calendar_id is not None and stage_lengths is not None:
        raise ValueError("Choose calendar_id or stage_lengths.")
    if stage_lengths is None:
        if calendar_id is None:
            calendar_id = profile.default_calendar_id
        calendar = next((item for item in profile.calendars if item.id == calendar_id), None)
        if calendar is None:
            options = ", ".join(item.id for item in profile.calendars)
            detail = (
                f" Choose one of {options} or provide stage_lengths."
                if options
                else " Provide stage_lengths."
            )
            raise ValueError(f"No matching calendar for {crop_id!r}.{detail}")
        stage_lengths = calendar.stage_lengths
    else:
        stage_lengths = _STAGE_LENGTHS.validate_python(stage_lengths)

    flowering = profile.flowering
    return ResolvedProfile(
        root_depth_m=profile.root_depth_m,
        root_depth_limited=profile.root_depth_limited,
        stage_lengths=stage_lengths,
        coefficients=profile.coefficients,
        depletion_fraction=profile.depletion_fraction,
        base_c=flowering.base_c if flowering else None,
        upper_c=flowering.upper_c if flowering else None,
        flowering_gdd=flowering.flowering_gdd if flowering else None,
        yield_factor=profile.yield_factor,
        calendar_id=calendar_id,
    )
