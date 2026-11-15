"""Explicit reviewed rulebooks. There is intentionally no universal default agronomy."""

from typing import Annotated, Literal

from field_shift_contracts.planning_v1.common import Capability, Contract, CropRef, DateWindow, Id
from pydantic import Field, model_validator


class CropRule(Contract):
    id: Id
    crop: CropRef
    family: Id
    region: Id
    source: str
    review_status: Literal["reviewed", "unreviewed", "synthetic_test"]
    valid_window: DateWindow
    purpose: Literal["new_sowing", "overwintering"]
    sowing_window: DateWindow
    growth_window: DateWindow
    base_temperature_c: float
    required_degree_days: Annotated[float, Field(ge=0)]
    require_unfrozen_sowing: bool
    min_seasonal_supply_mm: Annotated[float, Field(ge=0)]
    min_return_years: Annotated[int, Field(ge=1, le=20)]
    family_return_years: Annotated[int, Field(ge=1, le=20)]
    min_ph: Annotated[float, Field(ge=0, le=14)]
    max_ph: Annotated[float, Field(ge=0, le=14)]
    min_soil_depth_cm: Annotated[float, Field(gt=0, le=200)]
    irrigation_systems: list[str]
    required_capabilities: list[Capability]

    @model_validator(mode="after")
    def coherent(self):
        if self.min_ph > self.max_ph or not self.source or not self.irrigation_systems:
            raise ValueError("invalid rule thresholds/provenance")
        if not (
            self.valid_window.start
            <= self.sowing_window.start
            <= self.sowing_window.end
            <= self.valid_window.end
            and self.valid_window.start
            <= self.growth_window.start
            <= self.growth_window.end
            <= self.valid_window.end
        ):
            raise ValueError("rule windows outside validity")
        return self

    def applies(self, season: int):
        return self.growth_window.end.year == season


class Rulebook(Contract):
    version: Id
    rules: list[CropRule]

    @model_validator(mode="after")
    def unique_rules(self):
        keys = [(r.crop, r.growth_window.end.year, r.region) for r in self.rules]
        # Pydantic crop refs are mutable, so compare their serialized identities.
        keys = [(c.model_dump_json(), y, region) for c, y, region in keys]
        if len(keys) != len(set(keys)) or len({r.id for r in self.rules}) != len(self.rules):
            raise ValueError("duplicate rule scope")
        return self


def dates_in(window):
    from datetime import timedelta

    return [window.start + timedelta(days=i) for i in range((window.end - window.start).days + 1)]
