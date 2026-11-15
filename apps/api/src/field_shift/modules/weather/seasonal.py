from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from time import monotonic

from field_shift_science.fields.schemas import Polygon
from field_shift_science.weather.historical import (
    ARCHIVE_LAG_DAYS,
    ERA5_SPATIAL_RESOLUTION_DEGREES,
    OPEN_METEO_DOCUMENTATION,
    WeatherUnavailableError,
    load_archive,
    load_open_meteo,
)
from pydantic import BaseModel, ConfigDict, FiniteFloat, ValidationError
from shapely.geometry import Polygon as ShapePolygon

_EXPECTED_UNITS = {
    "temperature_2m_min": "°C",
    "temperature_2m_max": "°C",
    "precipitation_sum": "mm",
}
_FORECAST_DAYS = 7
_FORECAST_SOURCE_URL = "https://open-meteo.com/en/docs"


class _DailyArchive(BaseModel):
    time: list[date]
    temperature_2m_min: list[FiniteFloat | None]
    temperature_2m_max: list[FiniteFloat | None]
    precipitation_sum: list[FiniteFloat | None]


class _SeasonalArchive(BaseModel):
    latitude: FiniteFloat
    longitude: FiniteFloat
    timezone: str
    daily_units: dict[str, str]
    daily: _DailyArchive


class SeasonalWeatherDay(BaseModel):
    model_config = ConfigDict(frozen=True)

    date: date
    temperature_min_c: float | None = None
    temperature_max_c: float | None = None
    precipitation_mm: float | None = None


class DailyWeather(BaseModel):
    model_config = ConfigDict(frozen=True)

    start_date: date
    end_date: date
    grid_latitude: float
    grid_longitude: float
    timezone: str
    days: tuple[SeasonalWeatherDay, ...]


class FieldWeather(DailyWeather):
    field_id: str
    boundary_revision: int
    source: str
    source_url: str


class SeasonalWeather(FieldWeather):
    season: int
    source: str = "ECMWF ERA5"
    source_url: str = OPEN_METEO_DOCUMENTATION
    resolution_degrees: float = ERA5_SPATIAL_RESOLUTION_DEGREES


class WeatherForecast(FieldWeather):
    fetched_at: datetime
    source: str = "Open-Meteo Best Match"
    source_url: str = _FORECAST_SOURCE_URL


@lru_cache(maxsize=128)
def _archive(
    latitude: float, longitude: float, start: date, end: date, _cache_window: int
) -> DailyWeather:
    response = load_archive(
        {
            "latitude": f"{latitude:.6f}",
            "longitude": f"{longitude:.6f}",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": ",".join(_EXPECTED_UNITS),
            "models": "era5",
            "timezone": "auto",
        },
        _SeasonalArchive,
    )
    return _daily_weather(response, start, end)


def _daily_weather(response: _SeasonalArchive, start: date, end: date) -> DailyWeather:
    daily = response.daily
    if any(response.daily_units.get(key) != unit for key, unit in _EXPECTED_UNITS.items()) or not (
        len(daily.time)
        == len(daily.temperature_2m_min)
        == len(daily.temperature_2m_max)
        == len(daily.precipitation_sum)
    ):
        raise WeatherUnavailableError("Open-Meteo returned an invalid daily weather series.")

    by_date = {}
    for day, minimum, maximum, precipitation in zip(
        daily.time,
        daily.temperature_2m_min,
        daily.temperature_2m_max,
        daily.precipitation_sum,
        strict=True,
    ):
        if day in by_date or not start <= day <= end:
            raise WeatherUnavailableError("Open-Meteo returned invalid daily weather dates.")
        if minimum is not None and maximum is not None and minimum > maximum:
            minimum = maximum = None
        by_date[day] = SeasonalWeatherDay(
            date=day,
            temperature_min_c=minimum,
            temperature_max_c=maximum,
            precipitation_mm=precipitation
            if precipitation is not None and precipitation >= 0
            else None,
        )

    days = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        days.append(by_date[day] if day in by_date else SeasonalWeatherDay(date=day))

    return DailyWeather(
        start_date=start,
        end_date=end,
        grid_latitude=response.latitude,
        grid_longitude=response.longitude,
        timezone=response.timezone,
        days=tuple(days),
    )


def _field_centroid(geometry: dict | None) -> tuple[float, float]:
    try:
        ring = Polygon.model_validate(geometry).coordinates[0]
    except ValidationError:
        raise WeatherUnavailableError(
            "A valid field polygon is required for weather sampling."
        ) from None
    centroid = ShapePolygon(ring).centroid
    return round(centroid.y, 6), round(centroid.x, 6)


def load_seasonal_weather(
    field_id: str,
    boundary_revision: int,
    geometry: dict | None,
    season: int,
    *,
    today: date | None = None,
) -> SeasonalWeather | None:
    start = date(season, 1, 1)
    latest = (today or datetime.now(UTC).date()) - timedelta(days=ARCHIVE_LAG_DAYS)
    end = min(latest, date(season, 12, 31))
    if start > end:
        return None

    latitude, longitude = _field_centroid(geometry)
    archive = _archive(latitude, longitude, start, end, int(monotonic() // 3600))
    return SeasonalWeather(
        field_id=field_id,
        season=season,
        boundary_revision=boundary_revision,
        **archive.model_dump(),
    )


@lru_cache(maxsize=128)
def _forecast(
    latitude: float, longitude: float, _cache_window: int
) -> tuple[DailyWeather, datetime]:
    response = load_open_meteo(
        "https://api.open-meteo.com/v1/forecast",
        "https://customer-api.open-meteo.com/v1/forecast",
        {
            "latitude": f"{latitude:.6f}",
            "longitude": f"{longitude:.6f}",
            "daily": ",".join(_EXPECTED_UNITS),
            "forecast_days": str(_FORECAST_DAYS),
            "timezone": "auto",
        },
        _SeasonalArchive,
        unavailable_message="Open-Meteo weather forecast is unavailable.",
    )
    if not response.daily.time:
        raise WeatherUnavailableError("Open-Meteo weather forecast is unavailable.")
    start = min(response.daily.time)
    fetched_at = datetime.now(UTC)
    if abs((start - fetched_at.date()).days) > 1:
        raise WeatherUnavailableError("Open-Meteo returned invalid daily weather dates.")
    end = start + timedelta(days=_FORECAST_DAYS - 1)
    return _daily_weather(response, start, end), fetched_at


def load_weather_forecast(
    field_id: str, boundary_revision: int, geometry: dict | None
) -> WeatherForecast:
    latitude, longitude = _field_centroid(geometry)
    forecast, fetched_at = _forecast(latitude, longitude, int(monotonic() // 900))
    return WeatherForecast(
        field_id=field_id,
        boundary_revision=boundary_revision,
        fetched_at=fetched_at,
        **forecast.model_dump(),
    )
