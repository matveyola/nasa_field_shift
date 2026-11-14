"""Daily forcing and shared trajectories; probability weights require calibration."""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .common import (
    Contract,
    DateWindow,
    Finite,
    Fraction,
    Id,
    NonNegative,
    SourceRecord,
    Text,
    Utc,
    Versioned,
    unique,
)


class DailyWeather(Contract):
    date: date
    source_date: date | None
    lead_days: Annotated[int, Field(ge=0, le=2192)] | None
    tmin_c: Annotated[Finite, Field(ge=-100, le=65)] | None
    tmax_c: Annotated[Finite, Field(ge=-100, le=65)] | None
    liquid_rain_mm: Annotated[Finite, Field(ge=0, le=3000)] | None
    et0_mm: Annotated[Finite, Field(ge=0, le=100)] | None
    shortwave_mj_m2: Annotated[Finite, Field(ge=0, le=60)] | None
    wind_m_s: Annotated[Finite, Field(ge=0, le=150)] | None
    relative_humidity_fraction: Fraction | None
    snow_water_equivalent_mm: NonNegative | None
    soil_frozen: bool | None
    quality: Literal["valid", "partial", "missing", "rejected"]

    @model_validator(mode="after")
    def temperature_order(self):
        if self.tmin_c is not None and self.tmax_c is not None and self.tmin_c > self.tmax_c:
            raise ValueError("minimum temperature exceeds maximum")
        return self


class WeatherTrace(Contract):
    field_snapshot_id: Id
    grid_id: Id
    source_id: Id
    native_resolution_m: NonNegative
    representative_latitude: Annotated[Finite, Field(ge=-90, le=90)]
    representative_longitude: Annotated[Finite, Field(ge=-180, le=180)]
    days: Annotated[list[DailyWeather], Field(min_length=1, max_length=2192)]


class WeatherScenario(Contract):
    trajectory_id: Id
    member_id: Id
    weight: Fraction | None
    traces: Annotated[list[WeatherTrace], Field(min_length=1, max_length=30)]


class WeatherScenarioSet(Versioned):
    scenario_set_id: Id
    as_of: Utc
    issued_at: Utc
    cycle_at: Utc
    model_version: Id
    valid_window: DateWindow
    scenario_type: Literal["forecast", "seasonal", "historical_replay", "synthetic"]
    stale_after: Utc
    weight_calibration: Text | None
    sources: Annotated[list[SourceRecord], Field(min_length=1, max_length=200)]
    scenarios: Annotated[list[WeatherScenario], Field(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def coherent_trajectories(self):
        if not self.cycle_at <= self.issued_at <= self.as_of < self.stale_after:
            raise ValueError("require cycle <= issue <= as_of < stale_after")
        if (self.valid_window.end - self.valid_window.start).days >= 2192:
            raise ValueError("weather window exceeds execution bound")
        unique([s.id for s in self.sources], "weather source")
        unique([s.trajectory_id for s in self.scenarios], "trajectory")
        if any(s.available_at > self.as_of for s in self.sources):
            raise ValueError("weather source was unavailable at as_of")
        weights = [s.weight for s in self.scenarios]
        if self.weight_calibration is None:
            if any(w is not None for w in weights):
                raise ValueError("uncalibrated scenarios must have null weights")
        elif any(w is None for w in weights) or abs(sum(weights) - 1) > 1e-9:
            raise ValueError("calibrated weights must be complete and sum to 1")
        sources = {s.id for s in self.sources}
        expected_fields = {t.field_snapshot_id for t in self.scenarios[0].traces}
        for scenario in self.scenarios:
            unique([t.field_snapshot_id for t in scenario.traces], "trace field")
            if {t.field_snapshot_id for t in scenario.traces} != expected_fields:
                raise ValueError("all trajectories must cover the same fields")
            for trace in scenario.traces:
                if trace.source_id not in sources:
                    raise ValueError("unresolved weather source")
                dates = [d.date for d in trace.days]
                if (
                    dates[0] != self.valid_window.start
                    or dates[-1] != self.valid_window.end
                    or any((b - a).days != 1 for a, b in zip(dates, dates[1:]))
                ):
                    raise ValueError("daily dates must exactly cover the window without gaps")
                for day in trace.days:
                    if self.scenario_type in ("forecast", "seasonal"):
                        if day.lead_days != (day.date - self.cycle_at.date()).days:
                            raise ValueError("forecast lead must match cycle and valid date")
                    if self.scenario_type == "historical_replay":
                        if day.source_date is None or day.source_date > self.as_of.date():
                            raise ValueError("historical replay requires known original dates")
        return self
