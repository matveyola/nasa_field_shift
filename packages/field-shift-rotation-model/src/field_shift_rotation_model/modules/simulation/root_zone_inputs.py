from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from os import getenv

from field_shift_rotation_model.modules.geo_ml.era5_land import UNIT, MoistureSourceError
from field_shift_rotation_model.modules.geo_ml.ifs import (
    DEEP_MOISTURE,
    LIQUID_RAIN,
    MAX_TEMPERATURE,
    MIDDLE_MOISTURE,
    MIN_TEMPERATURE,
    ROOT_ZONE_VARIABLES,
    fetch_shared_ifs_payload,
    ifs_daily_values,
    ifs_run_url,
    parse_ifs_runs,
)
from field_shift_rotation_model.modules.weather.historical import WeatherUnavailableError


@dataclass(frozen=True, slots=True)
class RootZoneWeatherDay:
    date: date
    lead_days: int
    rain_mm: float
    reference_et0_mm: float
    temperature_c: float
    soil_temperature_c: float
    moisture_layers: tuple[float, float, float]
    temperature_min_c: float | None = None
    temperature_max_c: float | None = None


@dataclass(frozen=True, slots=True)
class RootZoneRun:
    initialized_on: date
    grid_latitude: float
    grid_longitude: float
    model_cycle: str
    initial_layers: tuple[float, float, float]
    days: tuple[RootZoneWeatherDay, ...]


def root_zone_storage_mm(layers: tuple[float, float, float], depth_m: float) -> float:
    if not 0 < depth_m <= 1 or not all(0 <= value <= 1 for value in layers):
        raise ValueError("Root-zone storage requires three valid IFS layers and depth up to 1 m.")
    thicknesses = (
        min(depth_m, 0.07),
        max(0.0, min(depth_m, 0.28) - 0.07),
        max(0.0, depth_m - 0.28),
    )
    return 1000 * sum(value * depth for value, depth in zip(layers, thicknesses, strict=True))


def root_zone_run_url(
    locations: Sequence[tuple[float, float]], initialized_on: date, api_key: str | None
) -> str:
    return ifs_run_url(locations, initialized_on, api_key, additional_daily=ROOT_ZONE_VARIABLES)


def parse_root_zone_runs(
    payload: object, locations: Sequence[tuple[float, float]], initialized_on: date
) -> tuple[RootZoneRun, ...]:
    try:
        ifs_runs = parse_ifs_runs(payload, locations, initialized_on)
        responses = payload if isinstance(payload, list) else [payload]
        output = []
        for response, run in zip(responses, ifs_runs, strict=True):
            deep = ifs_daily_values(response, DEEP_MOISTURE, UNIT)
            middle = ifs_daily_values(response, MIDDLE_MOISTURE, UNIT)
            rain = ifs_daily_values(response, LIQUID_RAIN, "mm", first_optional=True)
            try:
                low = ifs_daily_values(response, MIN_TEMPERATURE, "°C", first_optional=True)
                high = ifs_daily_values(response, MAX_TEMPERATURE, "°C", first_optional=True)
            except (KeyError, TypeError, ValueError):
                # Old cached or archived payloads can still drive the physical balance.
                low = high = [None] * (len(run.days) + 1)
            output.append(
                RootZoneRun(
                    initialized_on=run.initialized_on,
                    grid_latitude=run.grid_latitude,
                    grid_longitude=run.grid_longitude,
                    model_cycle=run.model_cycle,
                    initial_layers=(run.initial_moisture, middle[0], deep[0]),
                    days=tuple(
                        RootZoneWeatherDay(
                            day.date,
                            day.lead_days,
                            rain[day.lead_days],
                            day.et0_mm,
                            day.air_temperature_c,
                            day.soil_temperature_c,
                            (
                                day.surface_moisture,
                                day.subsurface_moisture,
                                deep[day.lead_days],
                            ),
                            low[day.lead_days],
                            high[day.lead_days],
                        )
                        for day in run.days
                    ),
                )
            )
        return tuple(output)
    except (KeyError, TypeError, ValueError, MoistureSourceError):
        raise WeatherUnavailableError("ECMWF IFS root-zone inputs are incomplete.") from None


def fetch_ifs_root_zone(
    latitude: float,
    longitude: float,
    initialized_on: date,
    *,
    fetch: Callable[[str], dict] | None = None,
) -> RootZoneRun:
    locations = [(latitude, longitude)]
    try:
        payload = (
            fetch_shared_ifs_payload(latitude, longitude, initialized_on)
            if fetch is None
            else fetch(root_zone_run_url(locations, initialized_on, getenv("OPEN_METEO_API_KEY")))
        )
    except MoistureSourceError:
        raise WeatherUnavailableError("ECMWF IFS root-zone forecast is unavailable.") from None
    return parse_root_zone_runs(payload, locations, initialized_on)[0]
