"""Download in-situ soil measurements and export daily UTC targets for training."""

import argparse
import csv
import json
import math
import re
import time
from collections import defaultdict
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, fields
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from io import StringIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from field_shift_ml.resources import evaluation_resource

USCRN = "https://www.ncei.noaa.gov/pub/data/uscrn/products"
AWDB = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1"
IFC = "https://www.hydroshare.org/resource/50c4ad3248864a03845c671ff8da968b/data/contents"
# The 2025 USDA crop layer identifies these monitoring sites as corn and soybean fields.
IFC_SITES = frozenset(("CROPCNTRL01", "WILSONFRM01", "WILSONFRM02"))
IFC_V2_PROTOCOL = evaluation_resource("iowa-v2-farm-sites.json")
MIDWEST = ("IA", "IL", "IN", "KS", "MI", "MN", "MO", "NE", "OH", "SD", "WI")
NOAA_FILENAME = re.compile(r'href="(CRNH0203-\d{4}-([A-Z]{2})_[\w.-]+\.txt)"')


@dataclass(frozen=True)
class Station:
    station_id: str
    latitude: float
    longitude: float
    state: str
    network: str
    location: str


@dataclass(frozen=True)
class Observation:
    station_id: str
    date: date
    latitude: float
    longitude: float
    moisture: float
    state: str
    network: str
    location: str
    samples: int


class DownloadCache:
    def __init__(self, directory: Path, refresh: bool):
        self.directory = directory
        self.refresh = refresh
        self.sources: dict[str, str] = {}

    def read(self, url: str) -> str:
        path = self.directory / sha256(url.encode()).hexdigest()
        if path.is_file() and not self.refresh:
            raw = path.read_bytes()
        else:
            for attempt in range(3):
                try:
                    request = Request(url, headers={"User-Agent": "FieldShift-Research/1.0"})
                    with urlopen(request, timeout=30) as response:
                        raw = response.read(16 * 1024 * 1024 + 1)
                    if not raw or len(raw) > 16 * 1024 * 1024:
                        raise ValueError(f"Unexpected response length from {url}")
                    break
                except (URLError, TimeoutError) as exc:
                    if isinstance(exc, HTTPError) and exc.code not in (429, 500, 502, 503, 504):
                        raise
                    if attempt == 2:
                        raise
                    time.sleep(2**attempt)
            write_atomic(path, raw)
        self.sources[url] = sha256(raw).hexdigest()
        return raw.decode("utf-8-sig")


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def daily_observations(
    station: Station,
    readings: Iterable[tuple[datetime, float]],
    start: date,
    end: date,
) -> list[Observation]:
    days: dict[date, dict[int, float]] = defaultdict(dict)
    for timestamp, moisture in readings:
        day = timestamp.date()
        if not start <= day <= end or not valid_moisture(moisture):
            continue
        hour = timestamp.hour
        if hour in days[day] and days[day][hour] != moisture:
            raise ValueError(f"Conflicting readings for {station.station_id} at {timestamp}")
        days[day][hour] = moisture
    return [
        Observation(
            station.station_id,
            day,
            station.latitude,
            station.longitude,
            round(math.fsum(hours.values()) / len(hours), 6),
            station.state,
            station.network,
            station.location,
            len(hours),
        )
        for day, hours in sorted(days.items())
        if len(hours) >= 20 and {hour // 6 for hour in hours} == {0, 1, 2, 3}
    ]


def valid_moisture(value: float) -> bool:
    return math.isfinite(value) and 0 < value <= 0.8


def uscrn_readings(raw: str) -> Iterable[tuple[str, datetime, float, float]]:
    for line in raw.splitlines():
        # NOAA can leave NUL-filled missing records in yearly fixed-width files.
        columns = line.lstrip("\x00").split()
        if not columns:
            continue
        if len(columns) != 38 or not re.fullmatch(r"[0-9]{5}", columns[0]):
            raise ValueError("Unexpected NOAA USCRN hourly record format")
        wban, utc_date, utc_time = columns[:3]
        # USCRN identifies intervals by the end of the observed hour.
        hour_start = datetime.strptime(utc_date + utc_time, "%Y%m%d%H%M") - timedelta(hours=1)
        yield wban, hour_start, float(columns[28]), float(columns[33])


def fetch_uscrn(
    cache: DownloadCache, states: tuple[str, ...], years: list[int], workers: int
) -> Iterable[Observation]:
    metadata = {
        row["WBAN"]: Station(
            f"USCRN:{row['WBAN']}",
            float(row["LATITUDE"]),
            float(row["LONGITUDE"]),
            row["STATE"],
            "USCRN",
            " ".join((row["LOCATION"], row["VECTOR"], row["NAME"])).strip(),
        )
        for row in csv.DictReader(cache.read(f"{USCRN}/stations.tsv").splitlines(), delimiter="\t")
        if row["NETWORK"] == "USCRN" and row["STATE"] in states
    }
    files = []
    for year in years:
        listing = cache.read(f"{USCRN}/hourly02/{year}/")
        matches = NOAA_FILENAME.findall(listing)
        if not matches:
            raise ValueError(f"No station files in NOAA archive listing for {year}")
        files.extend((year, name) for name, state in matches if state in states)

    def fetch(entry: tuple[int, str]) -> list[Observation]:
        year, filename = entry
        raw = cache.read(f"{USCRN}/hourly02/{year}/{filename}")
        readings = list(uscrn_readings(raw))
        wbans = {wban for wban, _, _, _ in readings}
        if len(wbans) != 1:
            raise ValueError(f"Expected exactly one station in {filename}")
        station = metadata.get(wbans.pop())
        if station is None:
            return []  # This archive also lists the separate USRCRN network.
        return daily_observations(
            station,
            ((stamp, vwc) for _, stamp, vwc, temperature in readings if temperature > 0),
            date(year, 1, 1),
            date(year, 12, 31),
        )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for batch in executor.map(fetch, files):
            yield from batch


def awdb_url(path: str, **parameters: str) -> str:
    return f"{AWDB}/{path}?{urlencode(parameters)}"


def scan_readings(raw: str, triplet: str, utc_offset: float) -> Iterable[tuple[datetime, float]]:
    offset = timedelta(hours=utc_offset)
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError(f"Unexpected USDA response for {triplet}")
    for result in payload:
        if result["stationTriplet"] != triplet:
            raise ValueError(f"USDA returned a different station for {triplet}")
        for series in result["data"]:
            element = series["stationElement"]
            if (
                element["elementCode"],
                element["heightDepth"],
                element["durationName"],
                element["storedUnitCode"],
            ) != ("SMS", -2, "HOURLY", "pct"):
                raise ValueError(f"Unexpected USDA sensor definition for {triplet}")
            for record in series["values"]:
                if record.get("qcFlag") == "V" and record.get("value") is not None:
                    timestamp = datetime.fromisoformat(record["date"]) - offset
                    yield timestamp, float(record["value"]) / 100


def fetch_scan(
    cache: DownloadCache,
    states: tuple[str, ...],
    years: list[int],
    workers: int,
    growing_season_only: bool,
) -> Iterable[Observation]:
    metadata = json.loads(
        cache.read(
            awdb_url(
                "stations",
                stationTriplets=",".join(f"*:{state}:SCAN" for state in states),
                elements="SMS:-2",
            )
        )
    )
    if not isinstance(metadata, list) or not metadata:
        raise ValueError("No stations returned by the USDA metadata API")

    def fetch(entry: tuple[dict, int]) -> list[Observation]:
        row, year = entry
        triplet = row["stationTriplet"]
        station = Station(
            f"SCAN:{triplet}",
            row["latitude"],
            row["longitude"],
            row["stateCode"],
            "SCAN",
            row["name"],
        )
        start = date(year, 4, 1) if growing_season_only else date(year, 1, 1)
        end = date(year, 10, 31) if growing_season_only else date(year, 12, 31)
        raw = cache.read(
            awdb_url(
                "data",
                stationTriplets=triplet,
                elements="SMS:-2:1",
                duration="HOURLY",
                beginDate=(start - timedelta(days=1)).isoformat(),
                endDate=(end + timedelta(days=1)).isoformat(),
                returnFlags="true",
                returnSuspectData="false",
            )
        )
        return daily_observations(
            station, scan_readings(raw, triplet, row["dataTimeZone"]), start, end
        )

    requests = [(row, year) for row in metadata for year in years]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for batch in executor.map(fetch, requests):
            yield from batch


def ifc_readings(raw: str, start: date, end: date) -> Iterable[tuple[datetime, float]]:
    reader = csv.DictReader(StringIO(raw))
    if not {"time", "data.sm_surf0"}.issubset(reader.fieldnames or []):
        raise ValueError("Unexpected Iowa Flood Center soil moisture columns")
    hours: dict[datetime, dict[int, float]] = defaultdict(dict)
    for record in reader:
        value = record["data.sm_surf0"]
        if value in ("", "NA"):
            continue
        timestamp = datetime.fromisoformat(record["time"])
        if timestamp.utcoffset() is None:
            raise ValueError("Iowa Flood Center timestamp is missing its UTC offset")
        timestamp = timestamp.astimezone(UTC).replace(tzinfo=None)
        moisture = float(value)
        if not start <= timestamp.date() <= end or not valid_moisture(moisture):
            continue
        if timestamp.minute % 15 or timestamp.second or timestamp.microsecond:
            continue
        readings = hours[timestamp.replace(minute=0)]
        if timestamp.minute in readings and readings[timestamp.minute] != moisture:
            raise ValueError(f"Conflicting Iowa Flood Center measurements at {timestamp}")
        readings[timestamp.minute] = moisture
    for hour, readings in hours.items():
        if len(readings) >= 3:
            yield hour, math.fsum(readings.values()) / len(readings)


def fetch_ifc(
    cache: DownloadCache,
    start: date,
    end: date,
    *,
    locations: dict[str, tuple[float, float]] | None = None,
    workers: int = 1,
) -> Iterable[Observation]:
    requested = IFC_SITES if locations is None else locations.keys()
    station_rows = csv.DictReader(StringIO(cache.read(f"{IFC}/list_hydrostations.csv")))
    stations = {row["name"]: row for row in station_rows if row["name"] in requested}
    if stations.keys() != requested:
        raise ValueError("Iowa Flood Center catalog is missing an agricultural evaluation station")

    def fetch(entry: tuple[str, dict[str, str]]) -> list[Observation]:
        name, row = entry
        latitude, longitude = float(row["lat"]), float(row["long"])
        if locations is not None and (latitude, longitude) != locations[name]:
            raise ValueError(f"Iowa Flood Center coordinates have changed for {name}.")
        station = Station(f"IFC:{name}", latitude, longitude, "IA", "IFC", name)
        raw = cache.read(f"{IFC}/raw_data/{name}.csv")
        return daily_observations(station, ifc_readings(raw, start, end), start, end)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for batch in executor.map(fetch, sorted(stations.items())):
            yield from batch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--start-year", default=2021, type=int)
    parser.add_argument("--end-year", default=2025, type=int)
    parser.add_argument(
        "--network",
        choices=("all", "scan", "uscrn", "ifc", "ifc-v2"),
        default="all",
        help="all combines NOAA and USDA. IFC variants export separate Iowa evaluation cohorts.",
    )
    parser.add_argument("--states", nargs="+", choices=MIDWEST, default=MIDWEST)
    parser.add_argument("--workers", default=6, type=int)
    parser.add_argument("--growing-season-only", action="store_true", help="Keep April–October")
    parser.add_argument("--refresh", action="store_true", help="Re-fetch cached source files")
    args = parser.parse_args()
    if not 2012 <= args.start_year <= args.end_year <= date.today().year:
        parser.error("Use valid years from 2012 through the current year")
    if not 1 <= args.workers <= 8:
        parser.error("Workers must be between one and eight")

    years = list(range(args.start_year, args.end_year + 1))
    cache = DownloadCache(args.cache_dir, args.refresh)
    stations = tuple(sorted(set(args.states)))
    observations = []
    if args.network in ("all", "scan"):
        observations.extend(
            fetch_scan(cache, stations, years, args.workers, args.growing_season_only)
        )
    if args.network in ("all", "uscrn"):
        observations.extend(fetch_uscrn(cache, stations, years, args.workers))
    protocol_sha256 = None
    if args.network in ("ifc", "ifc-v2"):
        if "IA" not in stations:
            parser.error("The independent Iowa test data require IA in --states")
        stations = ("IA",)
        start, end = date(years[0], 1, 1), date(years[-1], 12, 31)
        locations = None
        if args.network == "ifc-v2":
            payload = IFC_V2_PROTOCOL.read_bytes()
            protocol_sha256 = sha256(payload).hexdigest()
            protocol = json.loads(payload)
            if (start.isoformat(), end.isoformat()) != (
                protocol["start_date"],
                protocol["end_date"],
            ):
                parser.error("The sealed v2 farm holdout uses the complete 2025 UTC calendar year.")
            if args.growing_season_only:
                parser.error("The sealed v2 farm holdout requires all dates.")
            locations = {
                row["station_id"].removeprefix("IFC:"): (row["latitude"], row["longitude"])
                for row in protocol["sites"]
            }
        observations.extend(fetch_ifc(cache, start, end, locations=locations, workers=args.workers))
    if args.growing_season_only:
        observations = [row for row in observations if 4 <= row.date.month <= 10]
    unique_days = {(row.station_id, row.date) for row in observations}
    if not observations or len(unique_days) != len(observations):
        raise ValueError("Expected nonempty, unique station-day observations")
    observations.sort(key=lambda row: (row.station_id, row.date))

    output = StringIO(newline="")
    writer = csv.DictWriter(
        output, [field.name for field in fields(Observation)], lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(asdict(row) for row in observations)
    data = output.getvalue().encode()
    manifest = {
        "start_year": args.start_year,
        "end_year": args.end_year,
        "network": args.network,
        "growing_season_only": args.growing_season_only,
        "states": stations,
        "rows": len(observations),
        "stations": len({row.station_id for row in observations}),
        "csv_sha256": sha256(data).hexdigest(),
        "source_sha256": cache.sources,
    }
    if protocol_sha256 is not None:
        manifest["protocol_sha256"] = protocol_sha256
    write_atomic(args.output, data)
    manifest_path = args.output.with_suffix(".manifest.json")
    write_atomic(manifest_path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    print(f"{manifest['rows']} station-days across {manifest['stations']} stations: {args.output}")


if __name__ == "__main__":
    main()
