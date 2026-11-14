"""Build daily 0–1 m root-zone targets from concurrent USDA SCAN probes."""

import argparse
import csv
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
from io import StringIO
from math import floor, fsum
from pathlib import Path

from field_shift_science.geo_ml.moisture_model import station_distance_km

from field_shift_ml.fetch_observations import DownloadCache, awdb_url, valid_moisture, write_atomic
from field_shift_ml.resources import evaluation_resource

PROTOCOL_PATH = evaluation_resource("root-zone-sites.json")
DEPTHS_INCHES = (-2, -4, -8, -20, -40)
DEPTHS_METRES = tuple(-value * 0.0254 for value in DEPTHS_INCHES)


@dataclass(frozen=True, slots=True)
class RootZoneSite:
    station_id: str
    name: str
    latitude: float
    longitude: float
    utc_offset: int
    role: str

    @property
    def block(self) -> tuple[int, int]:
        return floor(self.latitude / 2), floor(self.longitude / 2)


def select_sites(cache: DownloadCache, plan: dict) -> tuple[list[RootZoneSite], list[dict]]:
    roles = {
        **dict.fromkeys(plan["fit"], "fit"),
        **dict.fromkeys(plan["calibration"], "calibration"),
        **dict.fromkeys(plan["evaluation_farms"], "test"),
    }
    metadata_url = awdb_url("stations", stationTriplets=",".join(sorted(roles)), elements="SMS:-40")
    metadata = json.loads(cache.read(metadata_url))
    sites = [
        RootZoneSite(
            row["stationTriplet"],
            row["name"],
            row["latitude"],
            row["longitude"],
            row["dataTimeZone"],
            roles[row["stationTriplet"]],
        )
        for row in metadata
    ]
    missing = sorted(roles.keys() - {site.station_id for site in sites})
    excluded = [
        {"station_id": station_id, "reason": "catalog_unavailable"} for station_id in missing
    ]

    def separated(site: RootZoneSite, others: list[RootZoneSite]) -> bool:
        return all(
            site.block != other.block
            and station_distance_km(site.latitude, site.longitude, other.latitude, other.longitude)
            >= plan["spatial_buffer_km"]
            for other in others
        )

    holdout = [site for site in sites if site.role == "test"]
    calibration = [
        site for site in sites if site.role == "calibration" and separated(site, holdout)
    ]
    fit = [site for site in sites if site.role == "fit" and separated(site, holdout + calibration)]
    selected = fit + calibration + holdout
    excluded.extend(
        {"station_id": site.station_id, "reason": "spatial_buffer"}
        for site in sites
        if site not in selected
    )
    return sorted(selected, key=lambda site: site.station_id), excluded


def integrate_probes_mm(values: tuple[float, ...]) -> float:
    """Interpolate to one metre; the 40-inch SCAN probe is at 1.016 m."""
    total = DEPTHS_METRES[0] * values[0]
    for index, (left, right) in enumerate(zip(DEPTHS_METRES[:-1], DEPTHS_METRES[1:], strict=True)):
        width = min(right, 1.0) - left
        lower = values[index]
        upper = lower + (values[index + 1] - lower) * width / (right - left)
        total += width * (lower + upper) / 2
    return total * 1000


def daily_profiles(
    raw: str, site: RootZoneSite, start: date, end: date
) -> dict[date, tuple[float, int]]:
    readings: dict[datetime, dict[int, float]] = defaultdict(dict)
    for station in json.loads(raw):
        if station["stationTriplet"] != site.station_id:
            raise ValueError("Unexpected SCAN station")
        for series in station["data"]:
            descriptor = series["stationElement"]
            depth = descriptor["heightDepth"]
            if (
                descriptor["elementCode"] != "SMS"
                or descriptor["durationName"] != "HOURLY"
                or descriptor["storedUnitCode"] != "pct"
                or depth not in DEPTHS_INCHES
            ):
                raise ValueError("Unexpected SCAN profile element")
            for record in series["values"]:
                if record.get("qcFlag") != "V" or record.get("value") is None:
                    continue
                stamp = datetime.fromisoformat(record["date"]) - timedelta(hours=site.utc_offset)
                value = float(record["value"]) / 100
                if not start <= stamp.date() <= end or not valid_moisture(value):
                    continue
                previous = readings[stamp].setdefault(depth, value)
                if previous != value:
                    raise ValueError("Conflicting profile measurement at a station and UTC hour")

    days: dict[date, dict[int, float]] = defaultdict(dict)
    for stamp, profile in readings.items():
        if len(profile) == len(DEPTHS_INCHES):
            days[stamp.date()][stamp.hour] = integrate_probes_mm(
                tuple(profile[depth] for depth in DEPTHS_INCHES)
            )
    return {
        day: (round(fsum(hours.values()) / len(hours), 5), len(hours))
        for day, hours in sorted(days.items())
        if len(hours) >= 20 and {hour // 6 for hour in hours} == {0, 1, 2, 3}
    }


def observations(cache: DownloadCache, site: RootZoneSite, plan: dict) -> list[dict]:
    window = plan["evaluation"] if site.role == "test" else plan["fit_and_calibration"]
    start = date.fromisoformat(window["first_issue"]) + timedelta(days=1)
    end = date.fromisoformat(window["last_issue"]) + timedelta(days=5)
    raw = cache.read(
        awdb_url(
            "data",
            stationTriplets=site.station_id,
            elements=",".join(f"SMS:{depth}:1" for depth in DEPTHS_INCHES),
            duration="HOURLY",
            beginDate=(start - timedelta(days=1)).isoformat(),
            endDate=(end + timedelta(days=1)).isoformat(),
            returnFlags="true",
            returnSuspectData="false",
        )
    )
    result = daily_profiles(raw, site, start, end)
    print(f"{site.role} {site.station_id}: {len(result)} complete UTC profiles", flush=True)
    return [
        {
            "station_id": site.station_id,
            "date": day.isoformat(),
            "storage_mm": value,
            "hours": hours,
        }
        for day, (value, hours) in result.items()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    cache = DownloadCache(args.cache, args.refresh)
    protocol = PROTOCOL_PATH.read_bytes()
    plan = json.loads(protocol)
    sites, excluded = select_sites(cache, plan)
    with ThreadPoolExecutor(max_workers=5) as executor:
        batches = list(executor.map(lambda site: observations(cache, site, plan), sites))
    stream = StringIO()
    writer = csv.DictWriter(
        stream, fieldnames=["station_id", "date", "storage_mm", "hours"], lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(row for batch in batches for row in batch)
    encoded = stream.getvalue().encode()
    write_atomic(args.output, encoded)
    manifest = {
        "site_protocol_sha256": sha256(protocol).hexdigest(),
        "observations_sha256": sha256(encoded).hexdigest(),
        "sites": [
            asdict(site) | {"days": len(batch)} for site, batch in zip(sites, batches, strict=True)
        ],
        "excluded": excluded,
        "sources": cache.sources,
    }
    write_atomic(
        args.output.with_suffix(".manifest.json"),
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
    )


if __name__ == "__main__":
    main()
