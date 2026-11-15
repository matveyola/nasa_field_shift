"""Parse a pinned NASA POWER UTC daily response into explicit total-precipitation forcing.

POWER is coarse reanalysis/satellite-derived forcing, not field observations.
ET0 is derived with FAO-56 Penman-Monteith and daily mean RH (FAO eq.19),
NASA's representative elevation and 2m wind. This is not direct measured ET0.
"""

from datetime import datetime
from math import isfinite

from .meteo import eto_daily
from .weather import WeatherDay

UNITS = {
    "T2M_MAX": "C",
    "T2M_MIN": "C",
    "RH2M": "%",
    "WS2M": "m/s",
    "PRECTOTCORR": "mm/day",
    "ALLSKY_SFC_SW_DWN": "MJ/m^2/day",
}


def parse_power(document: dict, *, source_checksum: str) -> list[WeatherDay]:
    if len(source_checksum) != 64 or any(c not in "0123456789abcdef" for c in source_checksum):
        raise ValueError("Pinned lowercase SHA256 of original response required")
    if document["header"]["time_standard"] != "UTC":
        raise ValueError("UTC daily aggregation required")
    longitude, latitude, elevation = document["geometry"]["coordinates"]
    if not all(isfinite(v) for v in (longitude, latitude, elevation)) or not -90 <= latitude <= 90:
        raise ValueError("Valid representative coordinates/elevation required")
    if not -180 <= longitude <= 180 or not -500 <= elevation <= 9000:
        raise ValueError("Coordinate/elevation outside supported range")
    params = document["properties"]["parameter"]
    for key, unit in UNITS.items():
        if document["parameters"][key]["units"] != unit:
            raise ValueError(f"Unexpected units for {key}")
    keys = set(params["T2M_MAX"])
    if any(set(params[k]) != keys for k in UNITS):
        raise ValueError("Mismatched daily parameter dates")
    result = []
    fill = document["header"]["fill_value"]
    for key in sorted(keys):
        values = {k: params[k][key] for k in UNITS}
        if any(v == fill or not isfinite(v) for v in values.values()):
            raise ValueError(f"Missing/invalid NASA forcing on {key}; no zero filling")
        stamp = datetime.strptime(key, "%Y%m%d").date()
        low, high = values["T2M_MIN"], values["T2M_MAX"]
        rh, wind = values["RH2M"], values["WS2M"]
        solar, rain = values["ALLSKY_SFC_SW_DWN"], values["PRECTOTCORR"]
        if not -100 <= low <= high <= 65 or not 0 <= rh <= 100 or not 0 <= wind <= 150:
            raise ValueError("NASA weather outside physical range")
        if not 0 <= solar <= 60 or not 0 <= rain <= 3000:
            raise ValueError("Invalid radiation/precipitation")
        et0 = eto_daily(
            high, low, solar, wind, latitude, stamp.timetuple().tm_yday, elevation, rhmean=rh
        )
        result.append(
            WeatherDay(
                stamp,
                high,
                low,
                rain,
                et0,
                u2=wind,
                source="NASA-POWER-total-precip-" + source_checksum,
            )
        )
    if not result or any((b.date - a.date).days != 1 for a, b in zip(result, result[1:])):
        raise ValueError("NASA dates must be complete and daily")
    start = document["header"]["start"]
    end = document["header"]["end"]
    if result[0].date.strftime("%Y%m%d") != start or result[-1].date.strftime("%Y%m%d") != end:
        raise ValueError("NASA advertised window differs from daily records")
    return result
