"""Export quality-controlled five-centimetre observations for the sealed 50R1 cohort."""

import argparse
import csv
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, fields
from datetime import date, timedelta
from hashlib import sha256
from io import StringIO
from pathlib import Path

from field_shift.modules.geo_ml.ifs import HORIZON
from field_shift_ml.fetch_observations import (
    USCRN,
    DownloadCache,
    Observation,
    Station,
    awdb_url,
    daily_observations,
    scan_readings,
    uscrn_readings,
    write_atomic,
)
from field_shift_ml.resources import evaluation_resource

PROTOCOL = evaluation_resource("surface-forecast-50r1-sites.json")


def read_station(cache: DownloadCache, metadata: dict, start: date, end: date) -> list[Observation]:
    station = Station(
        **{field.name: metadata[field.name] for field in fields(Station)},
    )
    if station.network == "USCRN":
        raw = cache.read(f"{USCRN}/hourly02/{start.year}/{metadata['file']}")
        records = list(uscrn_readings(raw))
        if {f"USCRN:{wban}" for wban, *_ in records} != {station.station_id}:
            raise ValueError(f"NOAA archive station ID differs for {station.station_id}")
        readings = ((stamp, vwc) for _, stamp, vwc, temperature in records if temperature > 0)
    elif station.network == "SCAN":
        triplet = station.station_id.removeprefix("SCAN:")
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
        readings = scan_readings(raw, triplet, metadata["data_timezone_hours"])
    else:
        raise ValueError(f"Unsupported probe network {station.network}")
    return daily_observations(station, readings, start, end)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = PROTOCOL.read_bytes()
    manifest = json.loads(payload)
    first = date.fromisoformat(manifest["first_issue"]) + timedelta(days=1)
    last = date.fromisoformat(manifest["last_issue"]) + timedelta(days=HORIZON)
    cache = DownloadCache(args.cache, False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        observations = [
            row
            for batch in pool.map(
                lambda site: read_station(cache, site, first, last), manifest["sites"]
            )
            for row in batch
        ]
    observations.sort(key=lambda row: (row.station_id, row.date))
    if len({(row.station_id, row.date) for row in observations}) != len(observations):
        raise ValueError("Duplicate observation station-day in the sealed forecast audit")
    output = StringIO(newline="")
    writer = csv.DictWriter(
        output, [field.name for field in fields(Observation)], lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(asdict(row) for row in observations)
    body = output.getvalue().encode()
    counts = Counter(row.station_id for row in observations)
    metadata = {
        "protocol_sha256": sha256(payload).hexdigest(),
        "csv_sha256": sha256(body).hexdigest(),
        "source_sha256": cache.sources,
        "usable_days": {
            site["station_id"]: counts[site["station_id"]] for site in manifest["sites"]
        },
    }
    write_atomic(args.output, body)
    write_atomic(
        args.output.with_suffix(".manifest.json"),
        (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode(),
    )
    print(f"Prepared {len(observations)} station-days: {metadata['usable_days']}")


if __name__ == "__main__":
    main()
