"""Load UTC weather rows for the live and historical parts of a forecast."""

import json
import ssl
from dataclasses import dataclass, field
from datetime import date, timedelta
from http.client import HTTPException
from math import isfinite
from typing import Literal
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import certifi
from pydantic import TypeAdapter

from .historical_fields import (
    _MOISTURE_UNITS,
    _WEATHER_UNITS,
    ARCHIVE_URL,
    Fraction,
    WeatherDay,
)

LIVE_URL = "https://api.open-meteo.com/v1/forecast"
MAX_LIVE_DAYS = 16
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
UTC_TIMEZONES = {"GMT", "UTC", "Etc/GMT", "Etc/UTC"}

WeatherSource = Literal["live_forecast", "historical_analog"]
InitialTheta = tuple[float, float, float]
_THETA = TypeAdapter(tuple[Fraction, Fraction, Fraction])


class WeatherProviderError(RuntimeError):
    """A weather source could not supply the requested daily data."""


@dataclass(frozen=True)
class WeatherSeries:
    days: dict[date, WeatherDay]
    source_url: str
    source: WeatherSource
    grid_latitude: float
    grid_longitude: float
    initial_theta: dict[date, InitialTheta] = field(default_factory=dict)


def _download(url: str, source: WeatherSource) -> dict:
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    request = Request(url, headers={"User-Agent": "FieldShift weather provider/1.0"})
    try:
        with urlopen(request, timeout=20, context=context) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise WeatherProviderError(f"The {source} response is too large.")
        payload = json.loads(raw)
    except (OSError, ValueError, HTTPException) as error:
        raise WeatherProviderError(
            f"Could not download {source} weather: {error}"
        ) from error
    if not isinstance(payload, dict) or payload.get("error"):
        raise WeatherProviderError(f"The {source} provider did not return weather.")
    return payload


def _daily_series(
    latitude: float,
    longitude: float,
    start: date,
    end: date,
    source: WeatherSource,
) -> WeatherSeries:
    live = source == "live_forecast"
    units = {**_WEATHER_UNITS, **({"showers_sum": "mm"} if live else _MOISTURE_UNITS)}
    parameters = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start,
        "end_date": end,
        "timezone": "GMT",
        "cell_selection": "land",
        "daily": ",".join(units),
    }
    if not live:
        parameters["models"] = "era5_seamless"
    url = f"{LIVE_URL if live else ARCHIVE_URL}?{urlencode(parameters)}"
    payload = _download(url, source)

    try:
        if (
            payload["utc_offset_seconds"] != 0
            or payload["timezone"] not in UTC_TIMEZONES
        ):
            raise ValueError("expected UTC daily boundaries")
        grid_latitude = float(payload["latitude"])
        grid_longitude = float(payload["longitude"])
        if not (
            isfinite(grid_latitude)
            and isfinite(grid_longitude)
            and -90 <= grid_latitude <= 90
            and -180 <= grid_longitude <= 180
        ):
            raise ValueError("invalid grid coordinates")
        expected_units = {"time": "iso8601", **units}
        if any(
            payload["daily_units"].get(key) != unit
            for key, unit in expected_units.items()
        ):
            raise ValueError("unexpected or missing daily units")

        daily = payload["daily"]
        dates = [date.fromisoformat(value) for value in daily["time"]]
        expected = [
            start + timedelta(days=offset) for offset in range((end - start).days + 1)
        ]
        if dates != expected:
            raise ValueError("daily dates do not cover the complete requested period")
        if any(
            not isinstance(daily[key], list) or len(daily[key]) != len(dates)
            for key in units
        ):
            raise ValueError("daily weather columns have mismatched lengths")

        days = {}
        initial_theta = {}
        for index, day in enumerate(dates):
            rain = daily["rain_sum"][index]
            if live:
                # The forecast API reports convective showers separately from large-scale rain.
                showers = daily["showers_sum"][index]
                if rain < 0 or showers < 0:
                    raise ValueError("invalid liquid precipitation")
                rain += showers
            days[day] = WeatherDay(
                date=day,
                rain_mm=rain,
                low_c=daily["temperature_2m_min"][index],
                high_c=daily["temperature_2m_max"][index],
                reference_et0_mm=daily["et0_fao_evapotranspiration"][index],
            )
            if not live:
                theta = tuple(daily[column][index] for column in _MOISTURE_UNITS)
                if all(value is not None for value in theta):
                    initial_theta[day] = _THETA.validate_python(theta)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise WeatherProviderError(f"Invalid {source} weather: {error}") from error

    return WeatherSeries(
        days, url, source, grid_latitude, grid_longitude, initial_theta
    )


def fetch_live(
    latitude: float, longitude: float, *, as_of: date, days: int = 7
) -> WeatherSeries:
    """Fetch the near-term forecast from the given UTC day, inclusive."""
    if not 1 <= days <= MAX_LIVE_DAYS:
        raise ValueError(f"Live forecast length must be 1–{MAX_LIVE_DAYS} days.")
    end = as_of + timedelta(days=days - 1)
    return _daily_series(latitude, longitude, as_of, end, "live_forecast")


def fetch_history(
    latitude: float, longitude: float, start: date, end: date
) -> WeatherSeries:
    """Fetch a continuous ERA5-Seamless archive window, including soil moisture."""
    if start > end:
        raise ValueError(
            "Historical weather start date must be on or before its end date."
        )
    return _daily_series(latitude, longitude, start, end, "historical_analog")
