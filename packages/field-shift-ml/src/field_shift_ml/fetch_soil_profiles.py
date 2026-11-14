"""Build a coordinate-keyed, auditable SSURGO snapshot for training or validation."""

import argparse
import csv
import json
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from time import sleep

from field_shift_science.geo_ml.ssurgo import (
    SDA_ENDPOINT,
    SoilProfile,
    SoilSurveyError,
    fetch_soil_profile,
)

from field_shift_ml.fetch_observations import write_atomic


def station_locations(paths: list[Path]) -> dict[str, tuple[float, float]]:
    locations = {}
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                station = row["station_id"]
                location = round(float(row["latitude"]), 6), round(float(row["longitude"]), 6)
                if locations.setdefault(station, location) != location:
                    raise ValueError(f"Station {station} has multiple coordinates.")
    return dict(sorted(locations.items()))


def fetch_snapshot(
    locations: dict[str, tuple[float, float]], cache: Path, refresh: bool
) -> dict[str, dict | None]:
    def load(location):
        key = sha256(json.dumps(location).encode()).hexdigest()
        path = cache / f"{key}.json"
        if path.is_file() and not refresh:
            cached = json.loads(path.read_bytes())
            if cached is None:
                return None
            profile = SoilProfile.model_validate(cached)
            if (profile.latitude, profile.longitude) != location:
                raise ValueError(f"Cached soil survey location disagrees for {location}.")
            return profile.model_dump(mode="json")
        for attempt in range(3):
            try:
                profile = fetch_soil_profile(*location)
                break
            except SoilSurveyError:
                if attempt == 2:
                    raise
                sleep(2**attempt)
        result = profile.model_dump(mode="json") if profile else None
        write_atomic(path, json.dumps(result, sort_keys=True).encode())
        return result

    unique_locations = sorted(set(locations.values()))
    with ThreadPoolExecutor(max_workers=4) as executor:
        profiles = dict(zip(unique_locations, executor.map(load, unique_locations), strict=True))
    return {station: profiles[location] for station, location in locations.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stations", action="append", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    profiles = fetch_snapshot(station_locations(args.stations), args.cache, args.refresh)
    snapshot = json.dumps(profiles, indent=2, sort_keys=True, allow_nan=False).encode()
    write_atomic(args.output, snapshot)
    manifest = {
        "source": SDA_ENDPOINT,
        "stations": len(profiles),
        "profile_count": sum(profile is not None for profile in profiles.values()),
        "snapshot_sha256": sha256(snapshot).hexdigest(),
    }
    write_atomic(args.output.with_suffix(".manifest.json"), json.dumps(manifest, indent=2).encode())
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
