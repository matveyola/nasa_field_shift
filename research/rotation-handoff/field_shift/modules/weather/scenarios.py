from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Scenario(StrEnum):
    BASELINE = "baseline"
    DRY_YEAR = "dry_year"
    LESS_RAIN = "less_rain"


@dataclass(frozen=True, slots=True)
class WeatherDay:
    date: date
    rain_mm: float
    reference_use_mm: float


_DRY_RAIN = (0.0, 0.0, 0.0, 2.1, 2.1, 2.0, 2.0, 3.1, 3.4, 7.0)
_REFERENCE_USE = (2.0, 8.0, 6.2, 2.5, 2.5, 2.5, 2.5, 2.5, 2.5, 2.5)
_RAIN = {
    Scenario.BASELINE: (0.0, 3.0, 2.0, 3.5, 3.5, 4.0, 3.5, 3.1, 4.0, 8.0),
    Scenario.DRY_YEAR: _DRY_RAIN,
    Scenario.LESS_RAIN: tuple(round(amount * 0.7, 2) for amount in _DRY_RAIN),
}


def demo_weather(season: int, scenario: Scenario) -> list[WeatherDay]:
    """Return a synthetic ten-day scenario, never a live forecast."""
    return [
        WeatherDay(date(season, 8, day), rain, use)
        for day, (rain, use) in enumerate(zip(_RAIN[scenario], _REFERENCE_USE, strict=True), 1)
    ]
