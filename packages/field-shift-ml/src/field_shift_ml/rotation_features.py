"""Static and planned agronomic features for the rotation research benchmark."""

import csv
import json
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from hashlib import sha256
from math import exp, isfinite
from pathlib import Path
from statistics import fmean, median
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from field_shift.modules.geo_ml.ssurgo import SDA_ENDPOINT, SoilProfile
from field_shift.modules.rotations.yield_model import effect_features
from field_shift_ml.fetch_observations import write_atomic
from field_shift_ml.train_rotation_yield import Sample

DAYMET_URL = "https://daymet.ornl.gov/single-pixel/api/data"
DAYMET_SOURCE = "https://doi.org/10.3334/ORNLDAAC/2129"
CLIMATE_YEARS = range(1981, 2006)
MAX_CLIMATE_BYTES = 1_000_000
CLIMATE_COLUMNS = ("year", "yday", "prcp (mm/day)", "tmax (deg c)", "tmin (deg c)", "vp (Pa)")
CLIMATE_FEATURES = (
    "spring_precip_mm",
    "summer_precip_mm",
    "summer_dry_spell_days",
    "summer_vpd_kpa",
    "season_gdd_10_30",
    "summer_heat_degree_days_30",
    "spring_frost_days",
)
SOIL_FEATURES = (
    "sand_percent",
    "clay_percent",
    "organic_matter_percent",
    "bulk_density_g_cm3",
    "available_water_fraction",
    "natural_drainage_rank",
    "available_water_0_100_cm_mm",
    "available_water_25_100_cm_mm",
    "spring_water_table_depth_cm",
    "slope_percent",
)
PLAN_FEATURES = ("no_till_share", "drained_share", "three_crop_share")
GEOGRAPHY_FEATURES = ("latitude", "longitude")
FEATURE_NAMES = {
    "geo": GEOGRAPHY_FEATURES,
    "climate": CLIMATE_FEATURES,
    "soil": SOIL_FEATURES,
    "plan": PLAN_FEATURES,
}
Location = tuple[float, float]
Values = tuple[float | None, ...]
SOIL_AGGREGATE_COLUMNS = ("mukey", "aws025wta", "aws0100wta", "wtdepaprjunmin", "slopegradwta")
MAX_SOIL_AGGREGATE_BYTES = 32_768
_DRAINAGE_RANK = {
    label: float(index)
    for index, label in enumerate(
        (
            "very poorly drained",
            "poorly drained",
            "somewhat poorly drained",
            "moderately well drained",
            "well drained",
            "somewhat excessively drained",
            "excessively drained",
        )
    )
}


def location_of(sample: Sample) -> Location:
    return round(sample.latitude, 6), round(sample.longitude, 6)


def daymet_url(location: Location) -> str:
    params = urlencode(
        {
            "lat": f"{location[0]:.6f}",
            "lon": f"{location[1]:.6f}",
            "vars": "prcp,tmin,tmax,vp",
            "start": f"{CLIMATE_YEARS.start}-01-01",
            "end": f"{CLIMATE_YEARS.stop - 1}-12-31",
        },
        safe=",",
    )
    return f"{DAYMET_URL}?{params}"


def climate_path(cache: Path, location: Location) -> Path:
    return cache / (
        f"{location[0]:.6f}_{location[1]:.6f}_{CLIMATE_YEARS.start}-{CLIMATE_YEARS.stop - 1}.csv"
    )


def saturation_vapor_pressure(temperature_c: float) -> float:
    return 0.6108 * exp(17.27 * temperature_c / (temperature_c + 237.3))


def climate_profile(raw: bytes, location: Location, years: range = CLIMATE_YEARS) -> Values:
    """Aggregate complete historical Daymet years into fixed, pretrial covariates."""
    lines = iter(raw.decode("utf-8").splitlines())
    metadata = []
    for line in lines:
        if line == ",".join(CLIMATE_COLUMNS):
            break
        metadata.append(line)
    else:
        raise ValueError("Unexpected Daymet columns.")
    if not metadata or not any("Daymet Software Version 4" in line for line in metadata):
        raise ValueError("Unexpected Daymet version or columns.")
    location_tokens = metadata[0].split()
    if len(location_tokens) != 4 or location_tokens[::2] != ["Latitude:", "Longitude:"]:
        raise ValueError("Missing Daymet location.")
    returned = float(location_tokens[1]), float(location_tokens[3])
    if any(abs(a - b) > 0.0001 for a, b in zip(location, returned, strict=True)):
        raise ValueError("The Daymet file belongs to a different coordinate.")

    rows = csv.reader(lines)
    season_values = []
    for year in years:
        start = date(year, 1, 1)
        spring_rain = summer_rain = frost = heat = gdd = 0.0
        dry_run = max_dry_run = 0
        vpd = []
        for day_index in range(365):
            row = next(rows, [])
            if len(row) != len(CLIMATE_COLUMNS) or (int(row[0]), int(row[1])) != (
                year,
                day_index + 1,
            ):
                raise ValueError("Daymet requires every pretrial day in chronological order.")
            rain, high, low, vp = (float(value) for value in row[2:])
            if not (0 <= rain <= 1000 and -90 <= low <= high <= 65 and 0 <= vp <= 15000):
                raise ValueError("Invalid Daymet observation.")
            month = (start + timedelta(days=day_index)).month
            if month in (4, 5):
                spring_rain += rain
                frost += low < 0
            if 5 <= month <= 9:
                gdd += (max(10, min(high, 30)) + max(10, min(low, 30))) / 2 - 10
            if 6 <= month <= 8:
                summer_rain += rain
                heat += max(0.0, high - 30)
                dry_run = dry_run + 1 if rain < 1.0 else 0
                max_dry_run = max(max_dry_run, dry_run)
                saturation = (saturation_vapor_pressure(high) + saturation_vapor_pressure(low)) / 2
                vpd.append(max(0.0, saturation - vp / 1000))
        season_values.append((spring_rain, summer_rain, max_dry_run, fmean(vpd), gdd, heat, frost))
    if next(rows, None) is not None:
        raise ValueError("Daymet returned years outside the pretrial reference window.")
    return tuple(fmean(values) for values in zip(*season_values, strict=True))


def climate_snapshot(
    locations: Iterable[Location], cache: Path
) -> tuple[dict[Location, Values], dict[str, str]]:
    def load(location: Location) -> tuple[Location, Values, str]:
        path = climate_path(cache, location)
        cached = path.is_file()
        if cached:
            raw = path.read_bytes()
        else:
            request = Request(
                daymet_url(location), headers={"User-Agent": "FieldShift-Research/1.0"}
            )
            with urlopen(request, timeout=35) as response:
                raw = response.read(MAX_CLIMATE_BYTES + 1)
        if len(raw) > MAX_CLIMATE_BYTES:
            raise ValueError("Daymet response exceeded the research input limit.")
        values = climate_profile(raw, location)
        if not cached:
            write_atomic(path, raw)
        return location, values, sha256(raw).hexdigest()

    with ThreadPoolExecutor(max_workers=3) as executor:
        result = list(executor.map(load, sorted(set(locations))))
    return (
        {location: values for location, values, _ in result},
        {climate_path(cache, location).name: digest for location, _, digest in result},
    )


def aggregate_soil_request(keys: set[str], cache: Path) -> tuple[bytes, Path]:
    if not keys or any(not key.isascii() or not key.isdecimal() for key in keys):
        raise ValueError("SSURGO aggregate map units must have numeric keys.")
    csv_keys = ",".join(sorted(keys))
    query = (
        f"SELECT {', '.join(SOIL_AGGREGATE_COLUMNS)} FROM muaggatt "
        f"WHERE mukey IN ({csv_keys}) ORDER BY mukey"
    )
    data = json.dumps({"query": query, "format": "JSON+COLUMNNAME"}).encode()
    return data, cache / f"aggregates-{sha256(data).hexdigest()}.json"


def parse_soil_aggregates(raw: bytes, keys: set[str]) -> dict[str, Values]:
    document = json.loads(raw)
    table = document.get("Table", [])
    if not table or table[0] != list(SOIL_AGGREGATE_COLUMNS):
        raise ValueError("Unexpected SSURGO map-unit aggregates.")
    result = {}
    for key, shallow, deep, water_table, slope in table[1:]:
        key = str(key)
        if key not in keys or key in result:
            raise ValueError("Unexpected or duplicate SSURGO map unit.")

        def bounded(value: str | float | None, upper: float) -> float | None:
            if value is None:
                return None
            number = float(value)
            if not 0 <= number <= upper:
                raise ValueError("SSURGO aggregate property outside its documented units.")
            return number

        top_cm, total_cm = bounded(shallow, 25), bounded(deep, 100)
        if top_cm is not None and total_cm is not None and top_cm > total_cm:
            raise ValueError("SSURGO available water storage decreases with depth.")
        result[key] = (
            total_cm * 10 if total_cm is not None else None,
            (total_cm - top_cm) * 10 if total_cm is not None and top_cm is not None else None,
            bounded(water_table, 1000),
            bounded(slope, 1000),
        )
    return result


def aggregate_soil_snapshot(keys: set[str], cache: Path) -> tuple[dict[str, Values], str | None]:
    if not keys:
        return {}, None
    data, path = aggregate_soil_request(keys, cache)
    cached = path.is_file()
    if cached:
        raw = path.read_bytes()
    else:
        request = Request(
            SDA_ENDPOINT,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "FieldShift-Research/1.0"},
        )
        with urlopen(request, timeout=20) as response:
            raw = response.read(MAX_SOIL_AGGREGATE_BYTES + 1)
    if len(raw) > MAX_SOIL_AGGREGATE_BYTES:
        raise ValueError("The SSURGO aggregate response exceeds its cache limit.")
    values = parse_soil_aggregates(raw, keys)
    if not cached:
        write_atomic(path, raw)
    return values, sha256(raw).hexdigest()


def soil_values(profile: SoilProfile | None, aggregate: Values | None = None) -> Values:
    if profile is None:
        return (None,) * len(SOIL_FEATURES)
    capacity, wilting = profile.field_capacity, profile.wilting_point
    return (
        profile.sand,
        profile.clay,
        profile.organic_matter,
        profile.bulk_density,
        capacity - wilting
        if capacity is not None and wilting is not None and capacity > wilting
        else None,
        _DRAINAGE_RANK.get(profile.drainage.casefold()) if profile.drainage else None,
        *(aggregate if aggregate is not None else (None,) * 4),
    )


def feature_groups(
    row: Sample, climate: dict[Location, Values], soils: dict[Location, Values]
) -> dict[str, Values]:
    location = location_of(row)
    _, latitude, longitude, *_ = effect_features(row.latitude, row.longitude, row.preceding_crop)
    historical_climate = climate.get(location) if row.year >= CLIMATE_YEARS.stop else None
    return {
        "geo": (latitude, longitude),
        "climate": historical_climate
        if historical_climate is not None
        else (None,) * len(CLIMATE_FEATURES),
        "soil": soils.get(location, (None,) * len(SOIL_FEATURES)),
        "plan": (
            (row.plan.no_till_share, row.plan.drained_share, row.plan.three_crop_share)
            if row.plan is not None
            else (None,) * len(PLAN_FEATURES)
        ),
    }


def median_if_supported(values: Iterable[float | None]) -> float | None:
    finite = [value for value in values if value is not None and isfinite(value)]
    return median(finite) if finite else None
