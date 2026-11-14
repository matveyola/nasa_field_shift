"""Bounded public ARDN/POWER acquisition and retrospective preseason features.

All paths are explicit. POWER is a revised grid proxy, not a field observation
or archived issued forecast. Raw data remain outside installed packages.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PARAMETERS = {
    "T2M": "C",
    "T2M_MAX": "C",
    "T2M_MIN": "C",
    "RH2M": "%",
    "WS2M": "m/s",
    "PRECTOTCORR": "mm/day",
    "ALLSKY_SFC_SW_DWN": "MJ/m^2/day",
    "PS": "kPa",
}
CROPS = {"corn": "maize", "soybean": "soybean", "wheat": "wheat"}
OPERATIONS = {"corn": "plant_corn", "soybean": "plant_soy", "wheat": "plant_wheat"}
SOURCE_FILES = ("ardn-agro.csv", "ardn-sitemeta.csv", "ardn-ops_plant.csv", "ardn-ops_harv.csv")
CLIMATE_YEARS = (1984, 2009)
MIN_COVERAGE = 0.95


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_csv(path: Path, required: set[str]) -> list[dict[str, str]]:
    with path.open(encoding="cp1252", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames or []
        if len(columns) != len(set(columns)) or not required <= set(columns):
            raise ValueError(f"Invalid columns: {path.name}")
        rows = []
        for row in reader:
            if None in row or any(row.get(k) is None for k in required):
                raise ValueError(f"Malformed row: {path.name}")
            if row.get("uniqueid") not in {"description", "units"}:
                rows.append(row)
    return rows


def finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (ValueError, TypeError):
        return False


def sites(data_dir: Path) -> dict[str, dict]:
    result = {}
    corners = ("NW", "SW", "SE", "NE")
    required = {"uniqueid", "state"} | {f"{c}.{d}" for c in corners for d in ("Lat", "Lon")}
    for row in read_csv(data_dir / "ardn-sitemeta.csv", required):
        pairs = [
            (float(row[f"{c}.Lat"]), float(row[f"{c}.Lon"]))
            for c in corners
            if finite(row[f"{c}.Lat"]) and finite(row[f"{c}.Lon"])
        ]
        if not pairs:
            raise ValueError("Site has no finite coordinate pair")
        lat = sum(p[0] for p in pairs) / len(pairs)
        lon = sum(p[1] for p in pairs) / len(pairs)
        if not math.isfinite(lat + lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Invalid site coordinates")
        name = row["uniqueid"]
        if name in result:
            raise ValueError(f"Duplicate site: {name}")
        result[name] = dict(
            latitude=lat,
            longitude=lon,
            state=row["state"],
            physical_site=name.split(".")[0],
            coordinate_method="mean_available_metadata_corner_pairs",
            coordinate_pairs=len(pairs),
        )
    # Conservatively merge experiment aliases and sites within roughly 2 km, transitively.
    names = sorted(result)
    groups = {name: {name} for name in names}
    for left in names:
        for right in names:
            a, b = result[left], result[right]
            if a["physical_site"] == b["physical_site"] or (
                abs(a["latitude"] - b["latitude"]) <= 0.02
                and abs(a["longitude"] - b["longitude"]) <= 0.02
            ):
                combined = groups[left] | groups[right]
                for name in combined:
                    groups[name] = combined
    for name in names:
        result[name]["physical_site"] = min(groups[name]).split(".")[0]
    return result


def power_url(latitude: float, longitude: float, start="19810101", end="20151231") -> str:
    query = dict(
        parameters=",".join(PARAMETERS),
        community="AG",
        latitude=latitude,
        longitude=longitude,
        start=start,
        end=end,
        format="JSON",
    )
    query["time-standard"] = "UTC"
    return "https://power.larc.nasa.gov/api/temporal/daily/point?" + urlencode(query)


def validate_power(
    document: dict, start="19810101", end="20151231"
) -> dict[str, dict[date, float]]:
    if document["header"]["time_standard"] != "UTC":
        raise ValueError("POWER must use explicit UTC day boundaries")
    if document["header"]["start"] != start or document["header"]["end"] != end:
        raise ValueError("Unexpected POWER date range")
    result = {}
    fill = document["header"]["fill_value"]
    for parameter, unit in PARAMETERS.items():
        if document["parameters"][parameter]["units"] != unit:
            raise ValueError(f"Unexpected POWER units: {parameter}")
        daily = document["properties"]["parameter"][parameter]
        series = {}
        for key, value in daily.items():
            day = datetime.strptime(key, "%Y%m%d").date()
            if not start <= key <= end or len(key) != 8:
                raise ValueError("POWER day outside declared range")
            if value != fill and finite(value):
                series[day] = float(value)
        result[parameter] = series
    return result


GLOBAL_POINTS = {
    "kyiv": (50.45, 30.52),
    "rio": (-22.91, -43.17),
    "singapore": (1.35, 103.82),
    "sahara": (23.0, 13.0),
    "antarctica": (-80.0, 0.0),
    "la_paz": (-16.5, -68.15),
}


def acquire_global_panel(cache_dir: Path, *, budget_bytes: int = 1024**3) -> dict:
    """Six requested climate stress points, never crop-suitability or yield labels."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    receipts, used = [], 0
    for name, (lat, lon) in GLOBAL_POINTS.items():
        path = cache_dir / f"global-{name}-power-2023-2024.json"
        url = power_url(lat, lon, "20230101", "20241231")
        if path.exists():
            raw, acquired = path.read_bytes(), None
        else:
            if used + 8 * 1024**2 > budget_bytes:
                raise ValueError("Acquisition byte budget exhausted")
            with urlopen(
                Request(url, headers={"User-Agent": "FieldShift-research/1.0"}), timeout=60
            ) as response:
                raw = response.read(8 * 1024**2 + 1)
            acquired = datetime.now(UTC).isoformat()
        if len(raw) > 8 * 1024**2 or used + len(raw) > budget_bytes:
            raise ValueError("Acquisition byte budget exceeded")
        doc = json.loads(raw)
        daily = validate_power(doc, "20230101", "20241231")
        actual_lon, actual_lat = doc["geometry"]["coordinates"][:2]
        if abs(actual_lat - lat) > 0.001 or abs(actual_lon - lon) > 0.001:
            raise ValueError("POWER location mismatch")
        if not path.exists():
            path.write_bytes(raw)
        used += len(raw)
        receipts.append(
            dict(
                point=name,
                latitude=lat,
                longitude=lon,
                file=path.name,
                sha256=digest(path),
                url=url,
                bytes=len(raw),
                acquired_at=acquired,
                units=PARAMETERS,
                header=doc["header"],
                valid_days={key: len(values) for key, values in daily.items()},
                role="gridded_weather_stress_input_without_yield_truth",
            )
        )
        (cache_dir / "global-panel-receipts.json").write_text(json.dumps(receipts, indent=2) + "\n")
    return dict(points=receipts, total_bytes=used)


def acquire_power(data_dir: Path, cache_dir: Path, *, budget_bytes: int = 1024**3) -> dict:
    """One sequential worker, 8 MiB/request and total caller budget; resumable cache."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    metadata = sites(data_dir)
    receipts, coordinates, used = [], {}, 0
    for name, site in sorted(metadata.items()):
        coordinate = (site["latitude"], site["longitude"])
        if coordinate in coordinates:
            receipts.append(dict(site=name, alias_of=coordinates[coordinate]))
            continue
        coordinates[coordinate] = name
        path = cache_dir / f"{name}-power-1981-2015.json"
        url = power_url(*coordinate)
        if path.exists():
            raw, retrieved = path.read_bytes(), None
        else:
            if used + 8 * 1024**2 > budget_bytes:
                raise ValueError("Acquisition byte budget exhausted")
            with urlopen(
                Request(url, headers={"User-Agent": "FieldShift-research/1.0"}), timeout=60
            ) as response:
                raw = response.read(8 * 1024**2 + 1)
            retrieved = datetime.now(UTC).isoformat()
        if len(raw) > 8 * 1024**2 or used + len(raw) > budget_bytes:
            raise ValueError("Acquisition byte budget exceeded")
        document = json.loads(raw)
        daily = validate_power(document)
        actual_lon, actual_lat = document["geometry"]["coordinates"][:2]
        if abs(actual_lat - coordinate[0]) > 0.001 or abs(actual_lon - coordinate[1]) > 0.001:
            raise ValueError("Cached POWER location does not match requested site")
        if not path.exists():
            path.write_bytes(raw)
        used += len(raw)
        receipts.append(
            dict(
                site=name,
                file=path.name,
                url=url,
                sha256=digest(path),
                bytes=len(raw),
                acquired_at=retrieved,
                header=document["header"],
                valid_days={k: len(v) for k, v in daily.items()},
                units=PARAMETERS,
                requested_coordinates=coordinate,
                returned_geometry=document["geometry"],
            )
        )
        (cache_dir / "power-receipts.json").write_text(json.dumps(receipts, indent=2) + "\n")
    return dict(power=receipts, total_bytes=used, site_metadata=metadata)


def _mean(series: dict[date, float], days: list[date]) -> float:
    values = [series[day] for day in days if day in series]
    if len(values) / len(days) < MIN_COVERAGE:
        raise ValueError("Insufficient POWER coverage")
    return sum(values) / len(values)


def weather_features(daily: dict[str, dict[date, float]], cutoff: date) -> dict[str, float]:
    """Pre-2010 climatology and lagged windows; never use post-cutoff observations."""
    if cutoff <= date(CLIMATE_YEARS[1], 12, 31):
        raise ValueError("Issue cutoff must be after the fixed climatology")
    result = {}
    start, end = date(CLIMATE_YEARS[0], 1, 1), date(CLIMATE_YEARS[1], 12, 31)
    climate = defaultdict(list)
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        climate[day.month].append(day)
    for parameter in PARAMETERS:
        for month in range(1, 13):
            result[f"climate_{parameter}_{month:02}"] = _mean(daily[parameter], climate[month])
        for window in (30, 90):
            days = [cutoff - timedelta(days=7 + n) for n in range(window)]
            result[f"recent_{parameter}_{window}"] = _mean(daily[parameter], days)
    return result


def issue_date(year: int, crop: str, lead: str) -> date:
    if crop not in CROPS.values() or lead not in {"early", "standard"}:
        raise ValueError("Unknown crop or issue schedule")
    if crop == "wheat":
        return date(year - 1, 7 if lead == "early" else 8, 1)
    return date(year, 2 if lead == "early" else 3, 1)


def build_panel(data_dir: Path, cache_dir: Path) -> tuple[list[dict], dict]:
    metadata = sites(data_dir)
    planted = defaultdict(set)
    harvested = defaultdict(set)
    operations = read_csv(
        data_dir / "ardn-ops_plant.csv",
        {"uniqueid", "plotid", "cropyear", "cashcrop", "operation", "date"},
    )
    for row in operations:
        if row["cashcrop"] in OPERATIONS and row["operation"] == OPERATIONS[row["cashcrop"]]:
            if row["date"] not in {"", "NA"}:
                planted[row["uniqueid"], row["plotid"], row["cropyear"], row["cashcrop"]].add(
                    date.fromisoformat(row["date"])
                )
    for row in read_csv(
        data_dir / "ardn-ops_harv.csv",
        {"uniqueid", "plotid", "cropyear", "cashcrop", "operation", "date"},
    ):
        crop = row["cashcrop"]
        if crop in OPERATIONS and row["operation"] == OPERATIONS[crop].replace(
            "plant_", "harvest_"
        ):
            if row["date"] not in {"", "NA"}:
                harvested[row["uniqueid"], row["plotid"], row["cropyear"], crop].add(
                    date.fromisoformat(row["date"])
                )
    raw = read_csv(
        data_dir / "ardn-agro.csv", {"uniqueid", "plotid", "year", "crop", "grain_yield"}
    )
    panel, exclusions, seen, feature_cache = [], Counter(), set(), {}
    daily_cache, coordinate_files = {}, {}
    source_hashes = {name: digest(data_dir / name) for name in SOURCE_FILES}
    for site, info in sorted(metadata.items()):
        coordinate = (info["latitude"], info["longitude"])
        path = cache_dir / f"{site}-power-1981-2015.json"
        if not path.exists() and coordinate in coordinate_files:
            path = coordinate_files[coordinate]
        if path.exists():
            coordinate_files[coordinate] = path
            document = json.loads(path.read_bytes())
            lon, lat = document["geometry"]["coordinates"][:2]
            if abs(lat - coordinate[0]) > 0.001 or abs(lon - coordinate[1]) > 0.001:
                raise ValueError("POWER location mismatch")
            daily_cache[site] = validate_power(document)
            source_hashes[path.name] = digest(path)
    raw_labels = Counter()
    for row in raw:
        if not finite(row["grain_yield"]) or float(row["grain_yield"]) < 0:
            exclusions["missing_or_invalid_yield"] += 1
            continue
        crop, year, site = row["crop"], int(row["year"]), row["uniqueid"]
        if crop not in CROPS or year not in range(2011, 2016):
            exclusions["outside_crop_year_scope"] += 1
            continue
        raw_labels[crop] += 1
        key = (site, row["plotid"], row["year"], crop)
        if key in seen:
            raise ValueError(f"Duplicate yield membership: {key}")
        seen.add(key)
        if len(planted[key]) != 1:
            exclusions["ambiguous_or_missing_sowing"] += 1
            continue
        sowing = next(iter(planted[key]))
        if len(harvested[key]) != 1:
            exclusions["ambiguous_or_missing_harvest"] += 1
            continue
        harvest = next(iter(harvested[key]))
        if not sowing < harvest or harvest.year != year:
            exclusions["invalid_sowing_harvest_order"] += 1
            continue
        if site not in daily_cache:
            exclusions["missing_power_site"] += 1
            continue
        for lead in ("early", "standard"):
            cutoff = issue_date(year, CROPS[crop], lead)
            if not cutoff < sowing or (sowing - cutoff).days > 365:
                exclusions[f"cutoff_not_presowing_{lead}"] += 1
                continue
            cache_key = (site, cutoff)
            if cache_key not in feature_cache:
                try:
                    feature_cache[cache_key] = weather_features(daily_cache[site], cutoff)
                except ValueError:
                    feature_cache[cache_key] = None
            if feature_cache[cache_key] is None:
                exclusions[f"insufficient_weather_{lead}"] += 1
                continue
            info = metadata[site]
            panel.append(
                dict(
                    id="|".join(key),
                    site=site,
                    physical_site=info["physical_site"],
                    state=info["state"],
                    year=year,
                    crop=CROPS[crop],
                    lead=lead,
                    cutoff=cutoff.isoformat(),
                    sowing_date=sowing.isoformat(),
                    harvest_date=harvest.isoformat(),
                    label_available_on=(harvest + timedelta(days=365)).isoformat(),
                    days_before_sowing=(sowing - cutoff).days,
                    yield_t_ha=float(row["grain_yield"]) / 1000,
                    features=dict(
                        feature_cache[cache_key],
                        crop=CROPS[crop],
                        latitude=info["latitude"],
                        longitude=info["longitude"],
                        year=float(year),
                    ),
                )
            )
    panel.sort(key=lambda r: (r["id"], r["lead"]))
    audit = dict(
        source_sha256=source_hashes,
        exclusions=dict(exclusions),
        raw_finite_labels_by_crop=dict(raw_labels),
        rows_with_issue_schedules=len(panel),
        unique_labels=len({r["id"] for r in panel}),
        site_metadata=metadata,
        units={"target": "dry t/ha", "weather": PARAMETERS},
        climate_years=CLIMATE_YEARS,
        min_weather_coverage=MIN_COVERAGE,
        weather_lag_days=7,
        label_basis="ARDN already dry; no moisture conversion",
    )
    return panel, audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--global-panel", action="store_true")
    args = parser.parse_args()
    report = acquire_power(args.data_dir, args.cache_dir)
    if args.global_panel:
        report["global_weather_panel"] = acquire_global_panel(
            args.cache_dir, budget_bytes=1024**3 - report["total_bytes"]
        )
    report["sources"] = {name: digest(args.data_dir / name) for name in SOURCE_FILES}
    args.receipts.parent.mkdir(parents=True, exist_ok=True)
    args.receipts.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
