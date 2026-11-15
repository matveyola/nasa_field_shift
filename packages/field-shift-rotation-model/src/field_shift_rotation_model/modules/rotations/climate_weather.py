"""Load daily NASA NEX-GDDP-CMIP6 v2 projections for the nearest 0.25° cell."""

import gzip
import json
import re
import ssl
import struct
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache, partial
from http.client import HTTPException
from importlib.resources import files
from io import BytesIO
from math import floor, isfinite
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request

import certifi

from field_shift_rotation_model.network import acquisition_time, build_opener

CLIMATE_MODELS = ("ACCESS-CM2", "MPI-ESM1-2-HR")
CLIMATE_SCENARIOS = ("ssp245", "ssp585")

_DATASET_URL = "https://www.nccs.nasa.gov/data-collections/nex-gddp-cmip6/"
_DOI_URL = "https://doi.org/10.7917/OFSG3345"
_SUBSET_URL = "https://ds.nccs.nasa.gov/thredds/ncss/grid/AMES/NEX/GDDP-CMIP6"
_VERSION = "2.0"
_MEMBER = "r1i1p1f1"
_UNITS = {
    "tasmin": "K",
    "tasmax": "K",
    "pr": "kg m-2 s-1",
    "hurs": "%",
    "rsds": "W m-2",
    "sfcWind": "m s-1",
}
_CALENDARS = {"proleptic_gregorian", "gregorian", "standard"}
_TIME_UNITS = re.compile(r"days since (\d{4})-(\d{1,2})-(\d{1,2})(?:[ T]00:00:00(?:\.\d+)?Z?)?")
_MAX_RESPONSE_BYTES = 256 * 1024
_DOWNLOADS = ThreadPoolExecutor(max_workers=4, thread_name_prefix="nex-gddp")
_BUNDLE_DIRECTORY = files("field_shift_rotation_model").joinpath("examples/climate")
_BUNDLED_CELLS = {
    (42.125, -93.875): "climate-weather.json.gz",
    (42.875, -95.625): "sutherland-weather.json.gz",
}


class ClimateWeatherError(RuntimeError):
    """NASA or the provided snapshot could not supply valid climate data."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Only the fixed NASA NCCS host is authorized for outbound downloads.
        return None


@dataclass(frozen=True)
class _VariableYear:
    url: str
    calendar: str
    units: str
    time_units: str
    license: str
    institution: str
    tracking_id: str
    days: tuple[float, ...]


def _nearest_cell(latitude: float, longitude: float) -> tuple[float, float]:
    if not isfinite(latitude) or not -60 <= latitude <= 90:
        raise ValueError("NASA NEX-GDDP-CMIP6 latitude coverage is 60°S to 90°N.")
    if not isfinite(longitude) or not -180 <= longitude <= 180:
        raise ValueError("Longitude must be in [-180, 180].")
    lat_index = min(floor((latitude + 60) * 4), 599)
    lon_index = floor((longitude + 180) * 4) % 1440
    return -59.875 + lat_index / 4, -179.875 + lon_index / 4


def _selection(
    requested: list[str] | None,
    allowed: tuple[str, ...],
    default: tuple[str, ...],
    label: str,
) -> tuple[str, ...]:
    selected = tuple(requested) if requested is not None else default
    if not selected or any(item not in allowed for item in selected):
        raise ValueError(f"Choose {label} from {', '.join(allowed)}.")
    if len(set(selected)) != len(selected):
        raise ValueError(f"Each climate {label} may be selected once.")
    return selected


@lru_cache(maxsize=len(_BUNDLED_CELLS))
def _bundle(filename: str) -> dict | None:
    try:
        with gzip.open(_BUNDLE_DIRECTORY / filename, "rt", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise ClimateWeatherError(f"The bundled climate snapshot {filename} is invalid.") from error


def _bundled_subset(
    cell: tuple[float, float],
    start: date,
    end: date,
    models: tuple[str, ...],
    scenarios: tuple[str, ...],
) -> tuple[dict, list[dict]] | None:
    filename = _BUNDLED_CELLS.get(cell)
    if filename is None:
        return None
    snapshot = _bundle(filename)
    if snapshot is None:
        return None
    first = date.fromisoformat(snapshot["requested_start"])
    last = date.fromisoformat(snapshot["requested_end"])
    if start < first or end > last:
        return None
    by_pair = {(s["model"], s["scenario"]): s for s in snapshot["series"]}
    if any((model, scenario) not in by_pair for model in models for scenario in scenarios):
        return None

    series = []
    for model in models:
        for scenario in scenarios:
            original = by_pair[model, scenario]
            first_day = (start - first).days
            stop_day = (end - first).days + 1
            rows = original["daily"][first_day:stop_day]
            _check_days(rows, start, end)
            series.append(
                {
                    **original,
                    "daily": rows,
                    "quality_start": first.isoformat(),
                    "quality_end": last.isoformat(),
                    "sources": [
                        source
                        for source in original["sources"]
                        if start.year <= source["year"] <= end.year
                    ],
                }
            )
    metadata = {key: value for key, value in snapshot.items() if key != "series"}
    # Callers must not be able to mutate cached rows or provenance for future requests.
    return deepcopy(metadata), deepcopy(series)


def _check_days(rows: list[dict], start: date, end: date) -> None:
    expected = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    if [row["date"] for row in rows] != expected:
        raise ClimateWeatherError("The climate data do not cover the complete requested period.")


def _subset_url(lat: float, lon: float, model: str, scenario: str, year: int, variable: str) -> str:
    filename = f"{variable}_day_{model}_{scenario}_{_MEMBER}_gn_{year}_v{_VERSION}.nc"
    query = urlencode(
        {
            "var": variable,
            "north": lat + 0.005,
            "south": lat - 0.005,
            "east": lon + 0.005,
            "west": lon - 0.005,
            "time_start": f"{year}-01-01T00:00:00Z",
            "time_end": f"{year}-12-31T23:59:59Z",
            "horizStride": 1,
            "accept": "netcdf3",
            "addLatLon": "true",
        }
    )
    return f"{_SUBSET_URL}/{model}/{scenario}/{_MEMBER}/{variable}/{filename}?{query}"


@lru_cache(maxsize=1)
def _opener():
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return build_opener(HTTPSHandler(context=context), _NoRedirect())


def _download(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "FieldShift NEX-GDDP-CMIP6/1.0"})
    for attempt in range(2):
        try:
            with _opener().open(request, timeout=15) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise ClimateWeatherError("NASA returned a larger than expected point subset.")
            if raw[:4] not in (b"CDF\x01", b"CDF\x02"):
                raise ClimateWeatherError("NASA did not return a NetCDF3 point subset.")
            return raw
        except (OSError, HTTPException) as error:
            retryable = not isinstance(error, HTTPError) or error.code in (408, 500, 502, 503, 504)
            if attempt or not retryable:
                raise ClimateWeatherError(
                    "NASA climate download failed: "
                    f"{error.reason if isinstance(error, HTTPError) else error}."
                ) from error
    raise AssertionError("The climate download did not return or raise.")


def _decode(
    raw: bytes,
    url: str,
    lat: float,
    lon: float,
    model: str,
    scenario: str,
    year: int,
    variable: str,
) -> _VariableYear:
    try:
        from scipy.io import netcdf_file
    except ImportError as error:
        raise ClimateWeatherError("Online climate downloads require the SciPy package.") from error

    with netcdf_file(BytesIO(raw), mmap=False) as nc:
        source = nc._attributes
        for key, expected in {
            "cmip6_source_id": model,
            "scenario": scenario,
            "version": _VERSION,
            "variant_label": _MEMBER,
        }.items():
            if source.get(key) != expected.encode():
                raise ValueError(f"unexpected {key}")

        variables = nc.variables
        expected_lon = lon % 360
        lats, lons = variables["lat"].data, variables["lon"].data
        if list(lats) != [lat] or [item % 360 for item in lons] != [expected_lon]:
            raise ValueError("the response is for a different climate cell")

        timeline = variables["time"]
        calendar = timeline.calendar.decode()
        time_units = timeline.units.decode()
        match = _TIME_UNITS.fullmatch(time_units)
        if calendar not in _CALENDARS or match is None:
            raise ValueError("unsupported time units or climate calendar")
        baseline = date(*(int(value) for value in match.groups()))
        first = (date(year, 1, 1) - baseline).days
        count = (date(year + 1, 1, 1) - date(year, 1, 1)).days
        offsets = timeline.data.tolist()
        if len(offsets) != count or not all(
            isfinite(value) and floor(value) == first + i for i, value in enumerate(offsets)
        ):
            raise ValueError("missing, duplicated, or misordered daily dates")

        column = variables[variable]
        if column.dimensions != ("time", "lat", "lon") or column.shape != (count, 1, 1):
            raise ValueError("unexpected variable dimensions")
        units = column.units.decode()
        if units != _UNITS[variable] or any(
            key in column._attributes for key in ("scale_factor", "add_offset")
        ):
            raise ValueError("unexpected variable units or encoding")
        values = tuple(column.data[:, 0, 0].tolist())
        if not all(isfinite(value) and abs(value) < 1e9 for value in values):
            raise ValueError("missing or invalid point values")

        return _VariableYear(
            url,
            calendar,
            units,
            time_units,
            source["cmip6_license"].decode(),
            source["cmip6_institution_id"].decode(),
            source["tracking_id"].decode(),
            values,
        )


@lru_cache(maxsize=512)
def _fetch_variable(
    lat: float, lon: float, model: str, scenario: str, year: int, variable: str
) -> _VariableYear:
    url = _subset_url(lat, lon, model, scenario, year, variable)
    try:
        return _decode(_download(url), url, lat, lon, model, scenario, year, variable)
    except (
        ClimateWeatherError,
        KeyError,
        AttributeError,
        ValueError,
        TypeError,
        IndexError,
        OSError,
        EOFError,
        OverflowError,
        struct.error,
    ) as error:
        raise ClimateWeatherError(
            f"Could not load {model} {scenario} {year} {variable} for {lat}, {lon}: {error}"
        ) from error


def _online_series(
    cell: tuple[float, float], model: str, scenario: str, start: date, end: date
) -> dict:
    rows = []
    sources = []
    calendars = set()
    quality = {"rh_clipped_days": 0, "wind_clipped_days": 0, "radiation_clipped_days": 0}
    lat, lon = cell
    for year in range(start.year, end.year + 1):
        fetch = partial(_fetch_variable, lat, lon, model, scenario, year)
        columns = dict(zip(_UNITS, _DOWNLOADS.map(fetch, _UNITS), strict=True))
        for name, column in columns.items():
            calendars.add(column.calendar)
            sources.append(
                {
                    "year": year,
                    "variable": name,
                    "url": column.url,
                    "license": column.license,
                    "time_units": column.time_units,
                    "tracking_id": column.tracking_id,
                    "institution": column.institution,
                }
            )

        year_start = date(year, 1, 1)
        first = max(start, year_start)
        last = min(end, date(year, 12, 31))
        for index in range((first - year_start).days, (last - year_start).days + 1):
            day = year_start + timedelta(days=index)
            tmin, tmax, pr, rh, solar, wind = (col.days[index] for col in columns.values())
            if tmin > tmax or pr < -1e-12:
                raise ClimateWeatherError(f"Inconsistent {model} {scenario} climate data on {day}.")
            quality["rh_clipped_days"] += int(not 0 <= rh <= 100)
            quality["wind_clipped_days"] += int(wind < 0)
            quality["radiation_clipped_days"] += int(solar < 0)
            rows.append(
                {
                    "date": day.isoformat(),
                    "tmin_c": round(tmin - 273.15, 5),
                    "tmax_c": round(tmax - 273.15, 5),
                    "rain_mm": round(max(0, pr) * 86400, 5),
                    "rh_mean_pct": round(max(0, min(100, rh)), 5),
                    "rs_mj_m2": round(max(0, solar) * 0.0864, 5),
                    "wind_10m_m_s": round(max(0, wind), 5),
                }
            )
    if len(calendars) != 1:
        raise ClimateWeatherError(f"Mixed calendars in {model} {scenario} climate data.")
    _check_days(rows, start, end)
    return {
        "model": model,
        "scenario": scenario,
        "grid_latitude": lat,
        "grid_longitude": lon,
        "member": _MEMBER,
        "calendar": calendars.pop(),
        "units": _UNITS.copy(),
        "licenses": sorted({source["license"] for source in sources}),
        "sources": sources,
        "daily": rows,
        "quality": quality,
        "quality_start": start.isoformat(),
        "quality_end": end.isoformat(),
    }


def load_climate_weather(
    latitude: float,
    longitude: float,
    start: date,
    end: date,
    models: list[str] | None = None,
    scenarios: list[str] | None = None,
) -> dict:
    """Load an inclusive NEX scenario period. Defaults to ACCESS-CM2 and both SSPs.

    Coverage is 60°S–90°N, 2015–2100 and at most 11 calendar years per call.
    NASA must provide all six variables for the selected cell and years. The
    packaged snapshots cover Marsden (2039-10-01 to 2049-12-31) and Sutherland
    (2039-10-01 to 2042-12-31 with ACCESS-CM2).
    No other source is substituted for unavailable NASA projection data.
    """
    cell = _nearest_cell(latitude, longitude)
    if not date(2015, 1, 1) <= start <= end <= date(2100, 12, 31):
        raise ValueError("Choose an inclusive date range from 2015 through 2100.")
    if end.year - start.year >= 11:
        raise ValueError("A climate request may span at most 11 calendar years.")
    chosen_models = _selection(models, CLIMATE_MODELS, CLIMATE_MODELS[:1], "models")
    chosen_scenarios = _selection(scenarios, CLIMATE_SCENARIOS, CLIMATE_SCENARIOS, "scenarios")

    bundled = _bundled_subset(cell, start, end, chosen_models, chosen_scenarios)
    if bundled is not None:
        metadata, series = bundled
    else:
        series = [
            _online_series(cell, model, scenario, start, end)
            for model in chosen_models
            for scenario in chosen_scenarios
        ]
        metadata = {
            "dataset": "NASA Earth Exchange NEX-GDDP-CMIP6",
            "version": _VERSION,
            "dataset_url": _DATASET_URL,
            "doi_url": _DOI_URL,
            "retrieved_utc": acquisition_time(),
            "grid_spacing_deg": 0.25,
            "temporal_resolution": "daily",
            "conversions": {
                "tasmin_tasmax": "K minus 273.15 = °C",
                "pr": "kg m-2 s-1 times 86,400 = mm/day total precipitation water equivalent",
                "rsds": "W m-2 times 0.0864 = MJ m-2 day-1",
                "hurs": "daily mean percent, restricted to [0, 100]",
                "sfcWind": "daily mean near-surface wind, m/s, assumed 10 m",
            },
        }
    return {
        **metadata,
        "latitude": latitude,
        "longitude": longitude,
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "delivery": "bundled_snapshot" if bundled is not None else "nasa_nccs_ncss",
        "series": series,
    }
