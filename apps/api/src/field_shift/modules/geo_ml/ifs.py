from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite
from os import getenv
from urllib.parse import urlencode

from field_shift.modules.geo_ml.era5_land import (
    UNIT,
    MoistureRequestRejected,
    MoistureSourceError,
    _fetch,
)
from field_shift.modules.geo_ml.forecast_cache import shared_forecast_cache

HORIZON = 5
MODEL = "ecmwf_ifs"
MODEL_NAME = "ECMWF IFS HRES"
MODEL_DOCUMENTATION = "https://open-meteo.com/en/docs/single-runs-api"
GRID_KM = 9
CURRENT_MODEL_CYCLE = "50R1"
# At 00:00 UTC the May 12 run still used 49R1. The update started at 06:00 UTC.
FIRST_NEW_CYCLE_MIDNIGHT_RUN = date(2026, 5, 13)
MIDDLE_MOISTURE = "soil_moisture_7_to_28cm_mean"
_UNITS = {
    "soil_moisture_0_to_7cm_mean": UNIT,
    MIDDLE_MOISTURE: UNIT,
    "soil_temperature_0_to_7cm_mean": "°C",
    "temperature_2m_mean": "°C",
    "precipitation_sum": "mm",
    "et0_fao_evapotranspiration": "mm",
}
DEEP_MOISTURE = "soil_moisture_28_to_100cm_mean"
LIQUID_RAIN = "rain_sum"
ROOT_ZONE_VARIABLES = (DEEP_MOISTURE, LIQUID_RAIN)


@dataclass(frozen=True, slots=True)
class IFSForecastDay:
    date: date
    lead_days: int
    surface_moisture: float
    subsurface_moisture: float
    soil_temperature_c: float
    air_temperature_c: float
    precipitation_mm: float
    et0_mm: float


@dataclass(frozen=True, slots=True)
class IFSForecastRun:
    initialized_on: date
    grid_latitude: float
    grid_longitude: float
    initial_moisture: float
    days: tuple[IFSForecastDay, ...]

    @property
    def model_cycle(self) -> str:
        return (
            CURRENT_MODEL_CYCLE if self.initialized_on >= FIRST_NEW_CYCLE_MIDNIGHT_RUN else "49R1"
        )


def ifs_run_url(
    locations: Sequence[tuple[float, float]],
    initialized_on: date,
    api_key: str | None,
    *,
    additional_daily: Sequence[str] = (),
) -> str:
    params = {
        "latitude": ",".join(f"{latitude:.6f}" for latitude, _ in locations),
        "longitude": ",".join(f"{longitude:.6f}" for _, longitude in locations),
        "elevation": ",".join("nan" for _ in locations),
        "models": MODEL,
        "run": f"{initialized_on}T00:00",
        "daily": ",".join((*_UNITS, *additional_daily)),
        "forecast_days": str(HORIZON + 1),
        "timezone": "GMT",
        "cell_selection": "nearest",
    }
    host = "single-runs-api.open-meteo.com"
    if api_key:
        host = "customer-single-runs-api.open-meteo.com"
        params["apikey"] = api_key
    return f"https://{host}/v1/forecast?{urlencode(params)}"


def _bounded(value: object, minimum: float, maximum: float) -> float:
    if type(value) not in (int, float) or not isfinite(value) or not minimum <= value <= maximum:
        raise ValueError("Missing or invalid forecast value")
    return float(value)


def _parse_run(
    response: dict, location: tuple[float, float], initialized_on: date
) -> IFSForecastRun:
    dates = [initialized_on + timedelta(days=lead) for lead in range(HORIZON + 1)]
    latitude = _bounded(response["latitude"], -90, 90)
    longitude = _bounded(response["longitude"], -180, 180)
    requested_latitude, requested_longitude = location
    longitude_offset = (longitude - requested_longitude + 180) % 360 - 180
    # Longitude cells widen toward the poles in the reduced Gaussian grid.
    if abs(latitude - requested_latitude) > 0.13 or abs(longitude_offset) > 0.8:
        raise ValueError("The forecast grid location does not match the request")
    if response["utc_offset_seconds"] != 0 or any(
        response["daily_units"][key] != unit for key, unit in _UNITS.items()
    ):
        raise ValueError("Unexpected forecast units or timezone")
    daily = response["daily"]
    if daily["time"] != [day.isoformat() for day in dates]:
        raise ValueError("Missing daily forecast timestamps")
    columns = [daily[key] for key in _UNITS]
    if any(not isinstance(column, list) or len(column) != len(dates) for column in columns):
        raise ValueError("The forecast has incomplete daily columns")

    initial_moisture = _bounded(columns[0][0], 0, 1)
    bounds = ((0, 1), (0, 1), (-90, 70), (-90, 70), (0, 1500), (0, 30))
    days = tuple(
        IFSForecastDay(
            day,
            lead,
            *(
                _bounded(column[lead], *limits)
                for column, limits in zip(columns, bounds, strict=True)
            ),
        )
        for lead, day in enumerate(dates[1:], start=1)
    )
    return IFSForecastRun(initialized_on, latitude, longitude, initial_moisture, days)


def parse_ifs_runs(
    payload: object, locations: Sequence[tuple[float, float]], initialized_on: date
) -> tuple[IFSForecastRun, ...]:
    try:
        responses = payload if isinstance(payload, list) else [payload]
        if len(responses) != len(locations):
            raise ValueError("Unexpected number of forecast locations")
        return tuple(
            _parse_run(response, location, initialized_on)
            for response, location in zip(responses, locations, strict=True)
        )
    except (KeyError, TypeError, ValueError) as error:
        raise MoistureSourceError(
            "ECMWF IFS did not supply complete daily soil moisture forecasts."
        ) from error


def ifs_daily_values(
    response: dict, variable: str, unit: str, *, first_optional: bool = False
) -> list:
    values = response["daily"][variable]
    if (
        response["daily_units"][variable] != unit
        or not isinstance(values, list)
        or len(values) != HORIZON + 1
    ):
        raise ValueError("Unexpected forecast units or length")
    maximum = 1 if unit == UNIT else 1500
    for value in values[int(first_optional) :]:
        _bounded(value, 0, maximum)
    return values


def _complete_root_zone(payload: dict) -> bool:
    try:
        ifs_daily_values(payload, MIDDLE_MOISTURE, UNIT)
        ifs_daily_values(payload, DEEP_MOISTURE, UNIT)
        ifs_daily_values(payload, LIQUID_RAIN, "mm", first_optional=True)
    except (KeyError, TypeError, ValueError):
        return False
    return True


def fetch_shared_ifs_payload(latitude: float, longitude: float, initialized_on: date) -> dict:
    locations = [(latitude, longitude)]
    key = getenv("OPEN_METEO_API_KEY")
    url = ifs_run_url(locations, initialized_on, key, additional_daily=ROOT_ZONE_VARIABLES)

    def load() -> dict:
        payload = _fetch(url)
        parse_ifs_runs(payload, locations, initialized_on)
        return payload

    cache = shared_forecast_cache()
    if cache is None:
        return load()
    identity = f"{'customer' if key else 'public'}:{initialized_on}:{latitude:.6f}:{longitude:.6f}"
    return cache.get_or_load(identity, load, cacheable=_complete_root_zone)


def fetch_ifs_run(
    latitude: float,
    longitude: float,
    initialized_on: date,
    *,
    fetch: Callable[[str], dict] | None = None,
) -> IFSForecastRun:
    locations = [(latitude, longitude)]
    if fetch is None and shared_forecast_cache() is not None:
        try:
            payload = fetch_shared_ifs_payload(latitude, longitude, initialized_on)
            return parse_ifs_runs(payload, locations, initialized_on)[0]
        except MoistureRequestRejected:
            pass
    url = ifs_run_url(locations, initialized_on, getenv("OPEN_METEO_API_KEY"))
    return parse_ifs_runs((fetch or _fetch)(url), locations, initialized_on)[0]
