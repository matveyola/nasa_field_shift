from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite
from os import getenv
from urllib.parse import urlencode

from field_shift_rotation_model.modules.geo_ml.era5_land import MoistureSourceError, _fetch

TEMPERATURE_HISTORY_URL = "https://open-meteo.com/en/docs/historical-forecast-api"
_MINIMUM = "temperature_2m_min"
_MAXIMUM = "temperature_2m_max"
MAX_HISTORY_DAYS = 210
# Task 2 maize demo. AquaCrop MaizeGDD method 3, with FAO-56 crop coefficients.
BASE_C, UPPER_C = 8.0, 30.0
FLOWERING_START_GDD, FLOWERING_END_GDD = 880, 1060
MAIZE_STAGE_LENGTHS = (30, 40, 50, 30)
MAIZE_COEFFICIENTS = (0.30, 1.20, 0.35)


@dataclass(frozen=True, slots=True)
class CropForecastCalendar:
    coefficients: dict[date, float]
    flowering: dict[date, bool]
    flowering_start: date | None
    flowering_end: date | None


def fetch_temperature_history(
    latitude: float, longitude: float, start: date, end: date
) -> list[tuple[date, float, float]]:
    count = (end - start).days + 1
    if not 0 < count <= MAX_HISTORY_DAYS:
        raise ValueError("Unsupported temperature history interval")
    key = getenv("OPEN_METEO_API_KEY")
    host = "historical-forecast-api.open-meteo.com"
    params = dict(
        latitude=latitude,
        longitude=longitude,
        start_date=start,
        end_date=end,
        daily=f"{_MINIMUM},{_MAXIMUM}",
        timezone="GMT",
    )
    if key:
        host = f"customer-{host}"
        params["apikey"] = key
    payload = _fetch(f"https://{host}/v1/forecast?{urlencode(params)}")
    days = [start + timedelta(days=index) for index in range(count)]
    daily = payload["daily"]
    if (
        payload["utc_offset_seconds"] != 0
        or any(payload["daily_units"][name] != "°C" for name in (_MINIMUM, _MAXIMUM))
        or daily["time"] != [day.isoformat() for day in days]
    ):
        raise ValueError("Incomplete temperature history")
    return list(zip(days, daily[_MINIMUM], daily[_MAXIMUM], strict=True))


def stage_coefficient(
    day: int, lengths: tuple[int, int, int, int], values: tuple[float, float, float]
) -> float:
    initial_days, development_days, mid_days, late_days = lengths
    initial, mid, end = values
    development_end = initial_days + development_days
    mid_end = development_end + mid_days
    if day <= initial_days:
        return initial
    if day <= development_end:
        return initial + (mid - initial) * (day - initial_days) / development_days
    if day <= mid_end:
        return mid
    return mid + (end - mid) * min((day - mid_end) / late_days, 1.0)


def maize_coefficient(days_after_sowing: int) -> float:
    return stage_coefficient(days_after_sowing, MAIZE_STAGE_LENGTHS, MAIZE_COEFFICIENTS)


def growing_degree_days(low: float, high: float, base: float, upper: float) -> float:
    return max(0.0, (min(upper, max(base, high)) + min(upper, low)) / 2 - base)


def maize_forecast_calendar(
    planting_date: date,
    latitude: float,
    longitude: float,
    forecast: list[tuple[date, float | None, float | None]],
) -> CropForecastCalendar | None:
    if (
        not forecast
        or planting_date > forecast[0][0]
        or any(low is None or high is None for _, low, high in forecast)
    ):
        return None
    history_end = forecast[0][0] - timedelta(days=1)
    try:
        history = (
            fetch_temperature_history(latitude, longitude, planting_date, history_end)
            if history_end >= planting_date
            else []
        )
        heat = 0.0
        flowering_start = flowering_end = previous = None
        flowering = {}
        coefficients = {}
        for index, (day, low, high) in enumerate([*history, *forecast]):
            if (
                day != planting_date + timedelta(days=index)
                or low is None
                or high is None
                or not (isfinite(low) and isfinite(high) and -90 <= low <= high <= 70)
            ):
                return None
            heat += growing_degree_days(low, high, BASE_C, UPPER_C)
            in_flower = FLOWERING_START_GDD < heat <= FLOWERING_END_GDD
            if in_flower and flowering_start is None:
                flowering_start = day
            if heat > FLOWERING_END_GDD and flowering_start is not None and flowering_end is None:
                flowering_end = previous
            if day >= forecast[0][0]:
                coefficients[day] = maize_coefficient(index + 1)
                flowering[day] = in_flower
            previous = day
        return CropForecastCalendar(coefficients, flowering, flowering_start, flowering_end)
    except (MoistureSourceError, KeyError, TypeError, ValueError):
        return None
