"""Local thermal reference for climate-driven crop development."""

import gzip
import json
from datetime import date, timedelta
from functools import lru_cache
from importlib.resources import files
from math import isfinite
from urllib.parse import urlencode

from field_shift_rotation_model.modules.rotations.global_soil import _ssl_context
from field_shift_rotation_model.modules.rotations.marsden_defaults import MARSDEN_COORDINATES
from field_shift_rotation_model.network import urlopen

REFERENCE_START = date(1991, 1, 1)
REFERENCE_END = date(2020, 12, 31)
POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
MARSDEN_REFERENCE = files("field_shift_rotation_model").joinpath(
    "examples/climate/calendar-weather.json"
)
SUTHERLAND_COORDINATES = (42.9269, -95.5391)
SUTHERLAND_REFERENCE = MARSDEN_REFERENCE.with_name("sutherland-calendar.json.gz")


class ClimateReferenceError(RuntimeError):
    pass


def load_thermal_reference(latitude: float, longitude: float) -> dict:
    if (latitude, longitude) == MARSDEN_COORDINATES:
        return json.loads(MARSDEN_REFERENCE.read_text(encoding="utf-8"))
    if (latitude, longitude) == SUTHERLAND_COORDINATES:
        with gzip.open(SUTHERLAND_REFERENCE, "rt", encoding="utf-8") as source:
            return json.load(source)
    return _load_power(round(latitude, 4), round(longitude, 4))


@lru_cache(maxsize=64)
def _load_power(latitude: float, longitude: float) -> dict:
    query = urlencode(
        {
            "parameters": "T2M_MAX,T2M_MIN",
            "community": "AG",
            "format": "JSON",
            "latitude": latitude,
            "longitude": longitude,
            "start": REFERENCE_START.strftime("%Y%m%d"),
            "end": REFERENCE_END.strftime("%Y%m%d"),
            "time-standard": "UTC",
        }
    )
    source = f"{POWER_URL}?{query}"
    try:
        with urlopen(source, timeout=40, context=_ssl_context()) as response:
            body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise ValueError("Unexpected size for NASA POWER point response.")
        data = json.loads(body)
        coordinates = data["geometry"]["coordinates"]
        lon, lat, elevation = coordinates
        if abs(lat - latitude) > 0.02 or abs((lon - longitude + 180) % 360 - 180) > 0.02:
            raise ValueError("NASA POWER returned an unexpected coordinate.")
        if not isfinite(elevation) or not -100 <= elevation <= 4000:
            raise ValueError("NASA POWER point elevation is unavailable or out of scope.")
        parameters = data["properties"]["parameter"]
        maxes, mins = parameters["T2M_MAX"], parameters["T2M_MIN"]
        for index in range((REFERENCE_END - REFERENCE_START).days + 1):
            key = (REFERENCE_START + timedelta(days=index)).strftime("%Y%m%d")
            if not -70 <= mins[key] <= maxes[key] <= 65:
                raise ValueError(f"Missing or invalid NASA POWER temperature for {key}.")
        return {
            "geometry": {"coordinates": coordinates},
            "header": {"start": REFERENCE_START.isoformat(), "end": REFERENCE_END.isoformat()},
            "properties": {"parameter": {"T2M_MIN": mins, "T2M_MAX": maxes}},
            "request_url": source,
            "source": {"url": source, "title": "NASA POWER daily T2M_MAX and T2M_MIN"},
        }
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ClimateReferenceError(
            "NASA POWER could not supply the local thermal reference."
        ) from error
