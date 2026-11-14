"""Acquire and derive causal predictors for the historical surface-moisture benchmark."""

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
from math import cos, isfinite, radians
from os import getenv
from pathlib import Path
from urllib.parse import urlencode

import numpy as np

from field_shift.modules.geo_ml.moisture_features import (
    FEATURE_NAMES,
    moisture_feature_url,
    parse_moisture_features,
)
from field_shift.modules.geo_ml.ssurgo import SoilProfile
from field_shift.modules.weather.historical import (
    OPEN_METEO_ARCHIVE,
    OPEN_METEO_CUSTOMER_ARCHIVE,
)
from field_shift_ml.train_moisture import Station, download_features, write_atomically

ERA5_COLUMNS = {
    "rain_sum": ("mm", 0, 1500),
    "et0_fao_evapotranspiration": ("mm", 0, 30),
    "relative_humidity_2m_mean": ("%", 0, 100),
    "shortwave_radiation_sum": ("MJ/m²", 0, 60),
    "wind_speed_10m_mean": ("km/h", 0, 500),
    "temperature_2m_max": ("°C", -90, 70),
    "temperature_2m_min": ("°C", -90, 70),
    "vapour_pressure_deficit_max": ("kPa", 0, 25),
}
ARCHIVE_HISTORY = 30
SURFACE = "soil_moisture_0_to_7cm_mean"
TERRAIN_URL = "https://api.open-meteo.com/v1/elevation"
DISTANCE_DEGREES = 0.00225  # About 250 m for the relief and slope proxies.
DRAINAGE = (
    "very poorly drained",
    "poorly drained",
    "somewhat poorly drained",
    "moderately well drained",
    "well drained",
    "somewhat excessively drained",
    "excessively drained",
)
SOIL_COLUMNS = (
    "sand",
    "silt",
    "clay",
    "organic_matter",
    "bulk_density",
    "field_capacity",
    "wilting_point",
    "horizon_bottom_cm",
    "component_percent",
)


@dataclass(frozen=True, slots=True)
class StationRows:
    station: Station
    dates: tuple[date, ...]
    actual: np.ndarray
    groups: dict[str, dict[str, np.ndarray]]
    digests: dict[str, str]


def days_between(start: date, end: date) -> tuple[date, ...]:
    return tuple(start + timedelta(days=step) for step in range((end - start).days + 1))


def cached_json(
    cache: Path,
    identity: str,
    validate: Callable[[dict], None],
    *,
    url: str | None,
) -> tuple[dict, str]:
    path = cache / f"{sha256(identity.encode()).hexdigest()}.json"
    if path.is_file():
        encoded = path.read_bytes()
        payload = json.loads(encoded)
    else:
        if url is None:
            raise FileNotFoundError(f"Missing offline feature cache entry {path.name}")
        payload = download_features(url)
        encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
    validate(payload)
    if not path.is_file():
        write_atomically(path, encoded.decode())
    return payload, sha256(encoded).hexdigest()


def era5_weather_url(station: Station, first: date, end: date, api_key: str | None = None) -> str:
    parameters = {
        "latitude": f"{station.latitude:.6f}",
        "longitude": f"{station.longitude:.6f}",
        "start_date": first.isoformat(),
        "end_date": end.isoformat(),
        "models": "era5",
        "daily": ",".join(ERA5_COLUMNS),
        "timezone": "GMT",
        "cell_selection": "nearest",
        "elevation": "nan",
    }
    if api_key:
        parameters["apikey"] = api_key
    endpoint = OPEN_METEO_CUSTOMER_ARCHIVE if api_key else OPEN_METEO_ARCHIVE
    return f"{endpoint}?{urlencode(parameters)}"


def parse_weather(payload: dict, station: Station, dates: tuple[date, ...]) -> None:
    latitude, longitude = payload["latitude"], payload["longitude"]
    if (
        not isfinite(latitude)
        or not isfinite(longitude)
        or abs(latitude - station.latitude) > 0.126
        or abs(longitude - station.longitude) > 0.126
        or payload["utc_offset_seconds"] != 0
        or payload["daily"]["time"] != [day.isoformat() for day in dates]
    ):
        raise ValueError(f"Unexpected ERA5 coordinates or calendar at {station.id}")
    for name, (unit, lower, upper) in ERA5_COLUMNS.items():
        values = payload["daily"][name]
        if (
            payload["daily_units"][name] != unit
            or len(values) != len(dates)
            or any(
                type(value) not in (int, float) or not lower <= value <= upper for value in values
            )
        ):
            raise ValueError(f"Unexpected ERA5 weather column {name} at {station.id}")
    if any(
        low > high
        for low, high in zip(
            payload["daily"]["temperature_2m_min"],
            payload["daily"]["temperature_2m_max"],
            strict=True,
        )
    ):
        raise ValueError(f"Inconsistent ERA5 temperature range at {station.id}")


def trailing(values: np.ndarray, window: int) -> np.ndarray:
    """Use the target day and its previous days, and retain missing initial windows."""
    result = np.full(len(values), np.nan)
    cumulative = np.insert(np.cumsum(values), 0, 0)
    result[window - 1 :] = cumulative[window:] - cumulative[:-window]
    return result


def soil_features(soil: SoilProfile | None, surface: np.ndarray) -> dict[str, np.ndarray]:
    def column(value: float | None) -> np.ndarray:
        return np.full(len(surface), value if value is not None else np.nan)

    values = {name: getattr(soil, name, None) for name in SOIL_COLUMNS}
    capacity, wilting = values["field_capacity"], values["wilting_point"]
    available = (
        capacity - wilting
        if capacity is not None and wilting is not None and capacity > wilting
        else None
    )
    drainage = (getattr(soil, "drainage", None) or "").casefold()
    return {
        **{name: column(value) for name, value in values.items()},
        "available_water_capacity": column(available),
        "drainage_rank": column(DRAINAGE.index(drainage) if drainage in DRAINAGE else None),
        "above_wilting_point": surface - column(wilting),
        "relative_available_water": (surface - column(wilting)) / column(available),
        "surface_by_clay": surface * column(values["clay"]) / 100,
        "surface_by_organic_matter": surface * column(values["organic_matter"]) / 100,
    }


def terrain_features(
    stations: list[Station], cache: Path, *, fetch: bool
) -> tuple[dict[Station, dict[str, float]], list[str]]:
    result = {}
    hashes = []
    for first in range(0, len(stations), 20):
        batch = stations[first : first + 20]
        points = []
        for station in batch:
            lat, lon = station.latitude, station.longitude
            step = DISTANCE_DEGREES
            width = step / cos(radians(lat))
            points.extend(
                (
                    (lat, lon),
                    (lat + step, lon),
                    (lat - step, lon),
                    (lat, lon + width),
                    (lat, lon - width),
                )
            )
        latitudes, longitudes = zip(*points, strict=True)
        url = (
            TERRAIN_URL
            + "?"
            + urlencode(
                {
                    "latitude": ",".join(f"{lat:.6f}" for lat in latitudes),
                    "longitude": ",".join(f"{lon:.6f}" for lon in longitudes),
                }
            )
        )

        def validate(payload: dict, size: int = len(points)) -> None:
            elevations = payload["elevation"]
            if len(elevations) != size or any(
                type(value) not in (int, float) or not -500 <= value <= 9000 for value in elevations
            ):
                raise ValueError("Missing or invalid terrain elevations")

        payload, digest = cached_json(cache, url, validate, url=url if fetch else None)
        hashes.append(digest)
        for offset, station in enumerate(batch):
            center, north, south, east, west = payload["elevation"][offset * 5 : offset * 5 + 5]
            neighbors = (north, south, east, west)
            result[station] = {
                "elevation_m": center,
                "slope_proxy": np.hypot(north - south, east - west) / 500,
                "relative_elevation_m": center - sum(neighbors) / len(neighbors),
                "local_relief_m": max(center, *neighbors) - min(center, *neighbors),
            }
    return result, hashes


def station_rows(
    station: Station,
    observations: dict[date, float],
    soil: SoilProfile | None,
    terrain: dict[str, float],
    moisture_cache: Path,
    weather_cache: Path,
    *,
    fetch: bool,
    end_at: date | None = None,
    allow_missing_weather: bool = False,
) -> StationRows:
    # Whole-year endpoints match the existing feature archive cache keys.
    start, end = date(min(observations).year, 1, 1), date(max(observations).year, 12, 31)
    if end_at is not None:
        end = min(end, end_at)
    if (
        soil is not None
        and max(abs(station.latitude - soil.latitude), abs(station.longitude - soil.longitude))
        > 0.0001
    ):
        raise ValueError(f"SSURGO coordinates do not match {station.id}")
    key = getenv("OPEN_METEO_API_KEY")
    frame = None

    def validate_moisture(payload: dict) -> None:
        nonlocal frame
        frame = parse_moisture_features(payload, station.latitude, station.longitude, start, end)

    land, land_hash = cached_json(
        moisture_cache,
        moisture_feature_url(station.latitude, station.longitude, start, end, None),
        validate_moisture,
        url=moisture_feature_url(station.latitude, station.longitude, start, end, key)
        if fetch
        else None,
    )
    assert frame is not None
    rows = tuple(day for day in frame.days if day.date in observations and not day.is_frozen)
    if not rows:
        raise ValueError(f"No paired thawed days for {station.id}")
    dates = tuple(day.date for day in rows)
    land_start = date.fromisoformat(land["daily"]["time"][0])
    land_index = np.asarray([(day - land_start).days for day in dates])
    raw_surface = np.asarray(land["daily"][SURFACE], dtype=float)
    common = dict(zip(FEATURE_NAMES, np.asarray([day.values for day in rows]).T, strict=True))
    surface = common["surface_moisture"]

    weather_start = start - timedelta(days=ARCHIVE_HISTORY)
    weather_dates = days_between(weather_start, end)
    try:
        archive, weather_hash = cached_json(
            weather_cache,
            era5_weather_url(station, weather_start, end),
            lambda payload: parse_weather(payload, station, weather_dates),
            url=era5_weather_url(station, weather_start, end, key)
            if fetch and not allow_missing_weather
            else None,
        )
    except FileNotFoundError:
        if not allow_missing_weather:
            raise
        archive, weather_hash = None, "not_cached"
    weather_index = np.asarray([(day - weather_start).days for day in dates])
    weather = {
        name: (
            np.asarray(archive["daily"][name], dtype=float)
            if archive is not None
            else np.full(len(weather_dates), np.nan)
        )
        for name in ERA5_COLUMNS
    }
    rain, et0 = weather["rain_sum"], weather["et0_fao_evapotranspiration"]
    water_balance = rain - et0
    atmosphere = {
        **{name: values[weather_index] for name, values in weather.items()},
        "temperature_range": (weather["temperature_2m_max"] - weather["temperature_2m_min"])[
            weather_index
        ],
        **{
            f"{name}_{window}d": trailing(values, window)[weather_index]
            for name, values in (("rain", rain), ("et0", et0), ("rain_minus_et0", water_balance))
            for window in (3, 7, 14, 30)
        },
        "wet_days_14d": trailing(np.where(np.isfinite(rain), (rain >= 1), np.nan), 14)[
            weather_index
        ],
        "temperature_excess_14d": trailing(
            np.maximum(0, (weather["temperature_2m_min"] + weather["temperature_2m_max"]) / 2 - 10),
            14,
        )[weather_index],
    }
    history = {
        **common,
        "surface_delta_1d": surface - raw_surface[land_index - 1],
        "surface_delta_3d": surface - raw_surface[land_index - 3],
        "surface_minus_subsurface": surface - common["subsurface_moisture"],
        "subsurface_minus_deep": common["subsurface_moisture"] - common["deep_moisture"],
        "shallow_minus_air_temperature": (
            common["shallow_soil_temperature"] - common["air_temperature"]
        ),
    }
    profiles = soil_features(soil, surface)
    interactions = {
        "rain_7d_by_clay": atmosphere["rain_7d"] * profiles["clay"] / 100,
        "drying_7d_by_water_capacity": (
            atmosphere["rain_minus_et0_7d"] * profiles["available_water_capacity"]
        ),
    }
    return StationRows(
        station,
        dates,
        np.asarray([observations[day] for day in dates]),
        {
            "raw": {"surface_moisture": surface},
            "land_history": {
                name: values for name, values in history.items() if name != "surface_moisture"
            },
            "weather": atmosphere,
            "soil": profiles,
            "soil_weather": interactions,
            "terrain": {name: np.full(len(dates), value) for name, value in terrain.items()},
            "geography": {
                "latitude": np.full(len(dates), station.latitude),
                "longitude": np.full(len(dates), station.longitude),
            },
        },
        {"era5_land": land_hash, "era5": weather_hash},
    )


def prepare_rows(
    observations: dict[Station, dict[date, float]],
    soils: dict[str, SoilProfile],
    moisture_cache: Path,
    external_cache: Path,
    *,
    fetch: bool,
    end_at: date | None = None,
    allow_missing_weather: bool = False,
) -> tuple[list[StationRows], list[str]]:
    stations = sorted(observations, key=lambda station: station.id)
    terrain, hashes = terrain_features(stations, external_cache / "elevation", fetch=fetch)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(
                station_rows,
                station,
                observations[station],
                soils.get(station.id),
                terrain[station],
                moisture_cache,
                external_cache / "weather",
                fetch=fetch,
                end_at=end_at,
                allow_missing_weather=allow_missing_weather,
            )
            for station in stations
        ]
        try:
            completed = (future.result() for future in as_completed(futures))
            by_station = {row.station: row for row in completed}
        except BaseException:
            for future in futures:
                future.cancel()
            raise
        return [by_station[station] for station in stations], hashes
