"""Caller-supplied daily forcing. No hidden weather generation or network fetches."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class WeatherDay:
    date: date
    tmax: float
    tmin: float
    rain_mm: float
    et0_mm: float
    rhmin: float | None = None
    u2: float | None = None
    source: str = ""
    gap: bool = False
