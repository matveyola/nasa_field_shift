from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Protocol

from field_shift.modules.crops.catalog import demo_crop_profile
from field_shift.modules.simulation.schemas import WaterDay, WaterResult, WaterSource
from field_shift.modules.weather.historical import WeatherUnavailableError
from field_shift.modules.weather.scenarios import Scenario, demo_weather

REFERENCE_FIELD_CAPACITY = 0.30
REFERENCE_WILTING_POINT = 0.15
FAO56_BUCKET_VERSION = "fao56-single-kc-bucket-v1"


class BucketWeatherDay(Protocol):
    date: date
    rain_mm: float
    reference_et0_mm: float


def fao56_days(
    weather: Iterable[BucketWeatherDay],
    *,
    initial_mm: float,
    capacity_mm: float,
    threshold_mm: float,
    coefficient: float,
    rainfall_multiplier: float = 1.0,
    irrigation_mm: float = 0.0,
    daily_coefficients: Mapping[date, float] | None = None,
    daily_irrigation_mm: Mapping[date, float] | None = None,
) -> list[WaterDay]:
    if daily_irrigation_mm is not None:
        if irrigation_mm:
            raise ValueError(
                "Supply either initial irrigation or a daily irrigation schedule."
            )
        if any(
            not isfinite(amount) or amount < 0
            for amount in daily_irrigation_mm.values()
        ):
            raise ValueError("Daily irrigation must be finite and nonnegative.")

    reserve = round(initial_mm, 4)
    days = []
    for index, sample in enumerate(weather):
        if not (
            isfinite(sample.rain_mm)
            and isfinite(sample.reference_et0_mm)
            and sample.rain_mm >= 0
            and sample.reference_et0_mm >= 0
        ):
            raise WeatherUnavailableError("The weather source returned invalid values.")
        opening = reserve
        rain = round(sample.rain_mm * rainfall_multiplier, 4)
        if daily_irrigation_mm is None:
            irrigation = round(irrigation_mm, 4) if index == 0 else 0.0
        else:
            irrigation = round(daily_irrigation_mm.get(sample.date, 0.0), 4)
        available = opening + rain + irrigation
        stress_coefficient = min(1.0, opening / threshold_mm)
        kc = (
            coefficient
            if daily_coefficients is None
            else daily_coefficients[sample.date]
        )
        water_use = min(
            available, round(sample.reference_et0_mm * kc * stress_coefficient, 4)
        )
        drainage = round(max(0.0, available - water_use - capacity_mm), 4)
        reserve = round(available - water_use - drainage, 4)
        days.append(
            WaterDay(
                date=sample.date,
                reserve_mm=reserve,
                opening_mm=opening,
                rain_mm=rain,
                irrigation_mm=irrigation,
                water_use_mm=water_use,
                drainage_mm=drainage,
                source_rain_mm=sample.rain_mm,
                reference_et0_mm=sample.reference_et0_mm,
            )
        )
    return days


@dataclass(frozen=True, slots=True)
class WaterInputs:
    field_id: str
    crop: str
    season: int
    scenario: Scenario
    irrigation_mm: float = 0
    source: WaterSource = WaterSource.DEMO
    start_date: date | None = None
    end_date: date | None = None
    geometry: dict | None = None
    initial_reserve_mm: float | None = None
    flowering_start: date | None = None
    flowering_end: date | None = None


class WaterModel(Protocol):
    def run(self, inputs: WaterInputs) -> WaterResult: ...


class DemoWaterModel:
    """Illustrative mass balance on a fixed synthetic weather series."""

    def run(self, inputs: WaterInputs) -> WaterResult:
        crop = demo_crop_profile(inputs.crop)
        threshold, capacity = 60.0, 100.0
        reserve = 76.0
        days: list[WaterDay] = []
        for index, weather in enumerate(demo_weather(inputs.season, inputs.scenario)):
            opening = reserve
            irrigation = inputs.irrigation_mm if index == 0 else 0
            available = opening + weather.rain_mm + irrigation
            use = min(
                available, round(weather.reference_use_mm * crop.water_use_factor, 2)
            )
            drainage = round(max(0, available - use - capacity), 2)
            reserve = round(available - use - drainage, 2)
            days.append(
                WaterDay(
                    date=weather.date,
                    reserve_mm=reserve,
                    opening_mm=opening,
                    rain_mm=weather.rain_mm,
                    irrigation_mm=irrigation,
                    water_use_mm=use,
                    drainage_mm=drainage,
                )
            )

        stress = [day.date for day in days if day.reserve_mm < threshold]
        return WaterResult(
            field_id=inputs.field_id,
            season=inputs.season,
            scenario=inputs.scenario,
            demo=True,
            threshold_mm=threshold,
            flowering_start=date(inputs.season, 8, crop.flowering_start_day),
            flowering_end=date(inputs.season, 8, crop.flowering_end_day),
            first_stress_date=stress[0] if stress else None,
            min_reserve_mm=min(day.reserve_mm for day in days),
            stress_days=len(stress),
            days=days,
        )


def stress_summary(result: WaterResult) -> tuple[int, int]:
    longest = current = sensitive = 0
    previous: date | None = None
    for day in result.days:
        if day.reserve_mm >= result.threshold_mm:
            current = 0
            previous = None
            continue
        current = current + 1 if previous and (day.date - previous).days == 1 else 1
        previous = day.date
        longest = max(longest, current)
        if result.flowering_start is not None and result.flowering_end is not None:
            sensitive += result.flowering_start <= day.date <= result.flowering_end
    return longest, sensitive
