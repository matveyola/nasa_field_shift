"""Source selection and compatible aggregation for the field weather dashboard."""

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from itertools import groupby
from math import isfinite
from statistics import fmean
from typing import Literal

from field_shift_science.weather.historical import (
    ARCHIVE_LAG_DAYS,
    WeatherUnavailableError,
    load_archive,
    load_open_meteo,
)
from pydantic import BaseModel

from .service import scope

Source = Literal["reanalysis", "forecast", "climate"]
Metric = Literal[
    "precipitation", "temperature_min", "temperature_max", "reference_et0", "surface_moisture"
]
Gap = Literal["archive_lag", "outside_coverage", "unsupported", "provider_gap"]

MAX_PERIOD_DAYS = 92
_FORECAST_DAYS = 16
_ARCHIVE_START = date(1940, 1, 1)
_CLIMATE_END = date(2049, 12, 31)
_DAY = timedelta(days=1)
_VARIABLES: dict[Metric, tuple[str, str]] = {
    "precipitation": ("precipitation_sum", "mm"),
    "temperature_min": ("temperature_2m_min", "°C"),
    "temperature_max": ("temperature_2m_max", "°C"),
    "reference_et0": ("et0_fao_evapotranspiration", "mm"),
    "surface_moisture": ("soil_moisture_0_to_7cm_mean", "m³/m³"),
}
_CLIMATE_METRICS = ("precipitation", "temperature_min", "temperature_max")
_SURFACE_START = date(1950, 1, 1)
_FORECAST_METRICS = (*_CLIMATE_METRICS, "reference_et0")


class ProviderDaily(BaseModel):
    utc_offset_seconds: Literal[0]
    daily: dict[str, list[date] | list[float | None]]
    daily_units: dict[str, str]


class MetricPoint(BaseModel):
    start: date
    end: date
    value: float


class MetricSpan(BaseModel):
    start: date
    end: date
    source: Source | None
    reason: Gap | None
    points: list[MetricPoint]


class WeatherDashboard(BaseModel):
    field_id: str
    boundary_revision: int
    start: date
    end: date
    generated_at: datetime
    series: dict[Metric, list[MetricSpan]]


@lru_cache(maxsize=256)
def _provider(
    source: Source,
    lat: float,
    lon: float,
    start: date,
    end: date,
    cache_slot: int,
) -> ProviderDaily:
    variables = {
        "reanalysis": _FORECAST_METRICS,
        "forecast": _FORECAST_METRICS,
        "climate": _CLIMATE_METRICS,
    }[source]
    params = {
        "latitude": str(lat),
        "longitude": str(lon),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": "GMT",
        "daily": ",".join(_VARIABLES[metric][0] for metric in variables),
    }
    if source == "reanalysis":
        return load_archive({**params, "models": "era5"}, ProviderDaily)
    if source == "forecast":
        return load_open_meteo(
            "https://api.open-meteo.com/v1/forecast",
            "https://customer-api.open-meteo.com/v1/forecast",
            params,
            ProviderDaily,
            unavailable_message="Open-Meteo daily forecast is unavailable.",
        )
    return load_open_meteo(
        "https://climate-api.open-meteo.com/v1/climate",
        "https://customer-climate-api.open-meteo.com/v1/climate",
        {**params, "models": "EC_Earth3P_HR"},
        ProviderDaily,
        unavailable_message="Open-Meteo HighResMIP data is unavailable.",
    )


@lru_cache(maxsize=128)
def _surface_provider(
    lat: float,
    lon: float,
    start: date,
    end: date,
    cache_slot: int,
) -> ProviderDaily:
    return load_archive(
        {
            "latitude": str(lat),
            "longitude": str(lon),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "timezone": "GMT",
            "models": "era5_land",
            "daily": _VARIABLES["surface_moisture"][0],
        },
        ProviderDaily,
    )


def _series(payload: ProviderDaily, metric: Metric) -> dict[date, float]:
    dates = payload.daily.get("time", [])
    if any(not isinstance(day, date) for day in dates) or dates != sorted(set(dates)):
        return {}
    name, unit = _VARIABLES[metric]
    values = payload.daily.get(name, [])
    if payload.daily_units.get(name) != unit or len(values) != len(dates):
        return {}
    return {
        day: value
        for day, value in zip(dates, values, strict=True)
        if isinstance(value, float)
        and isfinite(value)
        and (metric.startswith("temperature") or value >= 0)
        and (metric != "surface_moisture" or value <= 1)
    }


def _collect(
    payload: ProviderDaily, metrics: tuple[Metric, ...]
) -> dict[Metric, dict[date, float]]:
    result = {metric: _series(payload, metric) for metric in metrics}
    lows, highs = result.get("temperature_min", {}), result.get("temperature_max", {})
    for day in lows.keys() & highs.keys():
        if lows[day] > highs[day]:
            del lows[day]
            del highs[day]
    return result


def _load(source, lat, lon, start, end, cache_slot):
    if start > end:
        return {}
    try:
        payload = _provider(source, lat, lon, start, end, cache_slot)
    except WeatherUnavailableError:
        return {}
    return _collect(payload, _CLIMATE_METRICS if source == "climate" else _FORECAST_METRICS)


def _surface(lat, lon, start, end, cache_slot):
    if start > end:
        return {}
    try:
        payload = _surface_provider(lat, lon, start, end, cache_slot)
    except WeatherUnavailableError:
        return {}
    return {"surface_moisture": _series(payload, "surface_moisture")}


def _spans(rows: list[tuple[date, Source | None, Gap | None, float | None]]) -> list[MetricSpan]:
    spans = []
    for (source, reason), run in groupby(rows, key=lambda row: (row[1], row[2])):
        entries = list(run)
        points = []
        if source is not None:
            step = 7 if source == "climate" else 1
            for i in range(0, len(entries), step):
                batch = entries[i : i + step]
                points.append(
                    MetricPoint(
                        start=batch[0][0],
                        end=batch[-1][0],
                        value=fmean(value for _, _, _, value in batch if value is not None),
                    )
                )
        spans.append(
            MetricSpan(
                start=entries[0][0],
                end=entries[-1][0],
                source=source,
                reason=reason,
                points=points,
            )
        )
    return spans


def build(field_id, revision, geometry, *, start: date, end: date, now: datetime | None = None):
    now = now or datetime.now(UTC)
    today = now.date()
    archive_end = today - ARCHIVE_LAG_DAYS * _DAY
    forecast_end = today + (_FORECAST_DAYS - 1) * _DAY
    slot = int(now.timestamp()) // 900
    context = scope(field_id, revision, geometry, now)
    lat, lon = context["latitude"], context["longitude"]
    archive = _load("reanalysis", lat, lon, max(start, _ARCHIVE_START), min(end, archive_end), slot)
    archive.update(_surface(lat, lon, max(start, _SURFACE_START), min(end, archive_end), slot))
    forecast = _load("forecast", lat, lon, max(start, today), min(end, forecast_end), slot)
    dates = [start + i * _DAY for i in range((end - start).days + 1)]
    climate_days = [
        day
        for day in dates
        if today <= day <= _CLIMATE_END
        and (day > forecast_end or any(day not in forecast.get(m, {}) for m in _CLIMATE_METRICS))
    ]
    climate = (
        _load("climate", lat, lon, climate_days[0], climate_days[-1], slot) if climate_days else {}
    )
    series = {}
    for metric in _VARIABLES:
        rows = []
        for day in dates:
            if day <= archive_end:
                value = archive.get(metric, {}).get(day)
                first = _SURFACE_START if metric == "surface_moisture" else _ARCHIVE_START
                rows.append(
                    (day, "reanalysis", None, value)
                    if value is not None
                    else (
                        day,
                        None,
                        "outside_coverage" if day < first else "provider_gap",
                        None,
                    )
                )
            elif day < today:
                rows.append((day, None, "archive_lag", None))
            elif metric == "surface_moisture":
                rows.append((day, None, "unsupported", None))
            else:
                short = forecast.get(metric, {}).get(day) if day <= forecast_end else None
                long = climate.get(metric, {}).get(day) if day <= _CLIMATE_END else None
                if short is not None:
                    rows.append((day, "forecast", None, short))
                elif long is not None:
                    rows.append((day, "climate", None, long))
                elif day > max(forecast_end, _CLIMATE_END):
                    rows.append((day, None, "outside_coverage", None))
                else:
                    reason = (
                        "unsupported"
                        if metric not in _CLIMATE_METRICS and day > forecast_end
                        else "provider_gap"
                    )
                    rows.append((day, None, reason, None))
        series[metric] = _spans(rows)
    return WeatherDashboard(
        field_id=field_id,
        boundary_revision=revision,
        start=start,
        end=end,
        generated_at=now,
        series=series,
    )
