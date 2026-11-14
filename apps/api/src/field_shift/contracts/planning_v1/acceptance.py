"""Schemas for synthetic specifications and separately acquired empirical evidence."""

import json
from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import Field, JsonValue, TypeAdapter, model_validator

from .common import Checksum, Contract, DateWindow, Finite, Id, Text, Utc, unique
from .field import Latitude, Longitude
from .weather import DailyWeather


class WeatherRecipe(Contract):
    recipe: Literal["constant_daily", "constant_daily_on_window"]
    tmin_c: Annotated[Finite, Field(ge=-100, le=65)]
    tmax_c: Annotated[Finite, Field(ge=-100, le=65)]
    liquid_rain_mm: Annotated[Finite, Field(ge=0, le=3000)]
    et0_mm: Annotated[Finite, Field(ge=0, le=100)]
    shortwave_mj_m2: Annotated[Finite, Field(ge=0, le=60)]
    soil_frozen: bool | None = None
    window: tuple[date, date] | None = None

    @model_validator(mode="after")
    def bounds(self):
        if self.tmin_c > self.tmax_c:
            raise ValueError("recipe minimum temperature exceeds maximum")
        if self.recipe == "constant_daily_on_window" and self.window is None:
            raise ValueError("windowed recipe requires a window")
        if self.window and (
            self.window[1] < self.window[0] or (self.window[1] - self.window[0]).days >= 2192
        ):
            raise ValueError("invalid or oversized recipe window")
        return self


class CaseLocation(Contract):
    latitude: Latitude
    longitude: Longitude


class AcceptanceCase(Contract):
    id: Annotated[str, Field(pattern=r"^G[0-9]{2}$")]
    title: Text
    case_kind: Literal["synthetic_contract"]
    location: CaseLocation
    production_system: Literal["outdoor"]
    inputs: dict[str, JsonValue]
    expected_invariants: Annotated[list[Text], Field(min_length=1, max_length=20)]
    agronomist_reviewed: Literal[False]
    paired_with: str | None = None


class AcceptancePack(Contract):
    schema_version: Literal["planning-acceptance-v1"]
    created_at: date
    status: Literal["test_specification_not_executed"]
    scope: Text
    warning: Text
    required_fixture_expansion: Annotated[list[Text], Field(min_length=1, max_length=20)]
    weather_profiles: dict[Id, WeatherRecipe]
    cases: Annotated[list[AcceptanceCase], Field(min_length=24, max_length=24)]

    @model_validator(mode="after")
    def references(self):
        ids = [c.id for c in self.cases]
        unique(ids, "acceptance case")
        if set(ids) != {f"G{i:02d}" for i in range(1, 25)}:
            raise ValueError("frozen pack requires exactly G01–G24")
        symbolic = {"same_grid_same_issue", "identical_fixture", "otherwise_valid"}
        for case in self.cases:
            if case.paired_with and (case.paired_with not in ids or case.paired_with == case.id):
                raise ValueError("invalid paired case reference")
            weather = case.inputs.get("weather")
            if (
                weather is not None
                and weather not in self.weather_profiles
                and weather not in symbolic
            ):
                raise ValueError("unknown weather recipe/relational fixture reference")
            window = case.inputs.get("window")
            if window is not None:
                if not isinstance(window, list) or len(window) != 2:
                    raise ValueError("case window must have two dates")
                start, end = (date.fromisoformat(v) for v in window)
                if end < start:
                    raise ValueError("case window is reversed")
            if "times" in case.inputs:
                for time in case.inputs["times"]:
                    TypeAdapter(Utc).validate_json(json.dumps(time))
        return self


class EmpiricalSite(Contract):
    site_id: Id
    field_polygon_locator: Text
    geometry_sha256: Checksum
    issue_snapshot_locator: Text
    issue_snapshot_sha256: Checksum
    observations_locator: Text
    observations_sha256: Checksum
    permission: Text
    acquired_at: Utc
    synthetic: Literal[False]


class EmpiricalManifest(Contract):
    schema_version: Literal["planning-empirical-v1"]
    status: Literal["insufficient_data", "acquired_not_evaluated"]
    sites: Annotated[list[EmpiricalSite], Field(max_length=10000)]
    limitations: Annotated[list[Text], Field(min_length=1, max_length=50)]

    @model_validator(mode="after")
    def honest_status(self):
        unique([s.site_id for s in self.sites], "empirical site")
        if (self.status == "acquired_not_evaluated") != bool(self.sites):
            raise ValueError("manifest status must reflect acquired site evidence")
        return self


def expand_recipe(recipe: WeatherRecipe, window: DateWindow | None = None) -> list[DailyWeather]:
    """Expand explicit synthetic windows only; never infer seasons from coordinates."""
    if window is None:
        if recipe.window is None:
            raise ValueError("explicit window required for this recipe")
        window = DateWindow(start=recipe.window[0], end=recipe.window[1])
    if (window.end - window.start).days >= 2192:
        raise ValueError("recipe expansion exceeds 2192 days")
    if recipe.window and not recipe.window[0] <= window.start <= window.end <= recipe.window[1]:
        raise ValueError("requested dates exceed explicit recipe window")
    result = []
    for offset in range((window.end - window.start).days + 1):
        result.append(
            DailyWeather(
                date=window.start + timedelta(days=offset),
                source_date=None,
                lead_days=None,
                tmin_c=recipe.tmin_c,
                tmax_c=recipe.tmax_c,
                liquid_rain_mm=recipe.liquid_rain_mm,
                et0_mm=recipe.et0_mm,
                shortwave_mj_m2=recipe.shortwave_mj_m2,
                wind_m_s=None,
                relative_humidity_fraction=None,
                snow_water_equivalent_mm=None,
                soil_frozen=recipe.soil_frozen,
                quality="partial",
            )
        )
    return result
