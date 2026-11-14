from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from math import cos, pi, sin
from os import getenv
from statistics import fmean
from urllib.parse import urlencode

from field_shift.modules.geo_ml.era5_land import GRID_STEP, UNIT, MoistureSourceError, _fetch
from field_shift.modules.weather.historical import (
    OPEN_METEO_ARCHIVE,
    OPEN_METEO_CUSTOMER_ARCHIVE,
)

FEATURE_NAMES = (
    "surface_moisture",
    "subsurface_moisture",
    "deep_moisture",
    "surface_mean_3d",
    "surface_mean_7d",
    "shallow_soil_temperature",
    "air_temperature",
    "season_sin",
    "season_cos",
)
LOOKBACK_DAYS = 6
_COLUMNS = {
    "soil_moisture_0_to_7cm_mean": UNIT,
    "soil_moisture_7_to_28cm_mean": UNIT,
    "soil_moisture_28_to_100cm_mean": UNIT,
    "soil_temperature_0_to_7cm_mean": "°C",
    "temperature_2m_mean": "°C",
    "snow_depth_mean": "m",
}


@dataclass(frozen=True, slots=True)
class MoistureFeatures:
    date: date
    values: tuple[float, ...]
    is_frozen: bool

    @property
    def era5_moisture(self) -> float:
        return self.values[0]


@dataclass(frozen=True, slots=True)
class MoistureFeatureSeries:
    days: tuple[MoistureFeatures, ...]
    grid_latitude: float
    grid_longitude: float


def moisture_feature_url(
    latitude: float, longitude: float, start_date: date, end_date: date, api_key: str | None
) -> str:
    parameters = {
        "latitude": f"{latitude:.6f}",
        "longitude": f"{longitude:.6f}",
        "start_date": (start_date - timedelta(days=LOOKBACK_DAYS)).isoformat(),
        "end_date": end_date.isoformat(),
        "models": "era5_land",
        "daily": ",".join(_COLUMNS),
        "timezone": "GMT",
        "cell_selection": "nearest",
        "elevation": "nan",
    }
    if api_key:
        parameters["apikey"] = api_key
    endpoint = OPEN_METEO_CUSTOMER_ARCHIVE if api_key else OPEN_METEO_ARCHIVE
    return f"{endpoint}?{urlencode(parameters)}"


def parse_moisture_features(
    data: dict,
    latitude: float,
    longitude: float,
    start_date: date,
    end_date: date,
) -> MoistureFeatureSeries:
    first = start_date - timedelta(days=LOOKBACK_DAYS)
    expected_days = [first + timedelta(days=i) for i in range((end_date - first).days + 1)]
    try:
        grid_latitude = float(data["latitude"])
        grid_longitude = float(data["longitude"])
        if not (-90 <= grid_latitude <= 90 and -180 <= grid_longitude <= 180):
            raise ValueError
        longitude_distance = abs((grid_longitude - longitude + 180) % 360 - 180)
        if max(abs(grid_latitude - latitude), longitude_distance) > GRID_STEP / 2 + 0.001:
            raise ValueError
        if data["utc_offset_seconds"] != 0 or any(
            data["daily_units"][column] != unit for column, unit in _COLUMNS.items()
        ):
            raise ValueError
        daily = data["daily"]
        if daily["time"] != [day.isoformat() for day in expected_days]:
            raise ValueError
        columns = [daily[column] for column in _COLUMNS]
        if any(
            not isinstance(column, list) or len(column) != len(expected_days) for column in columns
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise MoistureSourceError(
            "ERA5-Land returned an invalid feature series for this location"
        ) from error

    features: list[MoistureFeatures] = []
    shallow = columns[0]
    for index in range(LOOKBACK_DAYS, len(expected_days)):
        day = expected_days[index]
        surface, subsurface, deep, soil_temperature, air_temperature, snow = (
            column[index] for column in columns
        )
        history = shallow[index - LOOKBACK_DAYS : index + 1]
        if not all(
            type(value) in (int, float) and 0 <= value <= 1
            for value in (*history, subsurface, deep)
        ):
            continue
        if not all(
            type(value) in (int, float) and -90 <= value <= 70
            for value in (soil_temperature, air_temperature)
        ) or not (type(snow) in (int, float) and 0 <= snow <= 30):
            continue
        season = 2 * pi * (day.timetuple().tm_yday - 1) / 365.2425
        features.append(
            MoistureFeatures(
                date=day,
                values=(
                    surface,
                    subsurface,
                    deep,
                    fmean(history[-3:]),
                    fmean(history),
                    soil_temperature,
                    air_temperature,
                    sin(season),
                    cos(season),
                ),
                is_frozen=soil_temperature < 2 or snow >= 0.01,
            )
        )
    return MoistureFeatureSeries(tuple(features), grid_latitude, grid_longitude)


def fetch_moisture_features(
    latitude: float,
    longitude: float,
    start_date: date,
    end_date: date,
    api_key: str | None = None,
    *,
    fetch: Callable[[str], dict] = _fetch,
) -> MoistureFeatureSeries:
    if start_date > end_date or (end_date - start_date).days > 366:
        raise ValueError("Request up to 367 consecutive UTC dates")
    url = moisture_feature_url(
        latitude, longitude, start_date, end_date, api_key or getenv("OPEN_METEO_API_KEY")
    )
    frame = parse_moisture_features(fetch(url), latitude, longitude, start_date, end_date)
    if not frame.days:
        raise MoistureSourceError("ERA5-Land supplied no complete feature day")
    return frame
