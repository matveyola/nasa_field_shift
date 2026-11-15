"""Cache lagged ERA5 weather and soil profiles for offline Water research."""

import argparse
import json
from datetime import date, timedelta
from hashlib import sha256
from math import isfinite
from os import getenv
from pathlib import Path
from statistics import fmean
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from field_shift_science.geo_ml.era5_land import UNIT, latest_archive_day
from field_shift_science.simulation.root_zone_inputs import root_zone_storage_mm
from field_shift_science.simulation.root_zone_model import DEPTH_M
from field_shift_science.weather.historical import OPEN_METEO_ARCHIVE, OPEN_METEO_CUSTOMER_ARCHIVE

from field_shift_ml.fetch_observations import write_atomic
from field_shift_ml.fetch_root_zone_observations import PROTOCOL_PATH, RootZoneSite
from field_shift_ml.train_moisture import write_atomically
from field_shift_ml.train_root_zone import load_targets

LOOKBACK = 28
HISTORY_FEATURES = (
    "era5_lagged_storage_mm",
    "era5_storage_mean_7d_mm",
    "era5_storage_mean_28d_mm",
    "era5_storage_change_7d_mm",
    "era5_ifs_initial_storage_gap_mm",
    "era5_net_rain_et0_7d_mm",
    "era5_net_rain_et0_28d_mm",
    "era5_dry_spell_days",
)
VARIABLES = {
    "soil_moisture_0_to_7cm_mean": (UNIT, 1),
    "soil_moisture_7_to_28cm_mean": (UNIT, 1),
    "soil_moisture_28_to_100cm_mean": (UNIT, 1),
    "rain_sum": ("mm", 1500),
    "et0_fao_evapotranspiration": ("mm", 30),
}


def archive_url(site: RootZoneSite, start: date, end: date, api_key: str | None = None) -> str:
    query = {
        "latitude": f"{site.latitude:.6f}",
        "longitude": f"{site.longitude:.6f}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "models": "era5",
        "daily": ",".join(VARIABLES),
        "timezone": "GMT",
        "cell_selection": "nearest",
        "elevation": "nan",
    }
    if api_key:
        query["apikey"] = api_key
    endpoint = OPEN_METEO_CUSTOMER_ARCHIVE if api_key else OPEN_METEO_ARCHIVE
    return f"{endpoint}?{urlencode(query)}"


def daily_history(payload: dict, site: RootZoneSite, start: date, end: date) -> dict:
    dates = [start + timedelta(days=index) for index in range((end - start).days + 1)]
    latitude, longitude = payload["latitude"], payload["longitude"]
    if (
        not isfinite(latitude)
        or not isfinite(longitude)
        or abs(latitude - site.latitude) > 0.126
        or abs((longitude - site.longitude + 180) % 360 - 180) > 0.126
        or payload["utc_offset_seconds"] != 0
        or payload["daily"]["time"] != [day.isoformat() for day in dates]
        or any(payload["daily_units"].get(key) != unit for key, (unit, _) in VARIABLES.items())
    ):
        raise ValueError("ERA5 history has unexpected coordinates, dates or units")
    columns = [payload["daily"][key] for key in VARIABLES]
    result = {}
    for day, row in zip(dates, zip(*columns, strict=True), strict=True):
        if any(value is None for value in row):
            continue
        if not all(
            type(value) in (float, int) and isfinite(value) and 0 <= value <= bound
            for value, (_, bound) in zip(row, VARIABLES.values(), strict=True)
        ):
            raise ValueError("ERA5 history contains invalid daily values")
        result[day.isoformat()] = [root_zone_storage_mm(row[:3], DEPTH_M), *row[3:]]
    return result


def history_features(archive: dict, issue: date, initial_storage: float) -> tuple:
    # Use the same six-day publication cutoff as the application archive.
    latest = latest_archive_day(issue)
    path = [
        archive.get((latest - timedelta(days=offset)).isoformat())
        for offset in reversed(range(LOOKBACK))
    ]
    if any(row is None for row in path):
        return (None,) * len(HISTORY_FEATURES)
    storage, rain, et0 = zip(*path, strict=True)
    return (
        storage[-1],
        fmean(storage[-7:]),
        fmean(storage),
        storage[-1] - storage[-7],
        initial_storage - storage[-1],
        sum(rain[-7:]) - sum(et0[-7:]),
        sum(rain) - sum(et0),
        next((count for count, value in enumerate(reversed(rain)) if value >= 1), len(rain)),
    )


def archive_station(site: RootZoneSite, start: date, end: date, cache: Path) -> dict:
    public = archive_url(site, start, end)
    path = cache / (sha256(public.encode()).hexdigest() + ".json")
    if path.exists():
        raw = path.read_bytes()
    else:
        request = Request(
            archive_url(site, start, end, getenv("OPEN_METEO_API_KEY")),
            headers={"Accept": "application/json", "User-Agent": "FieldShift-Research/1.0"},
        )
        try:
            with urlopen(request, timeout=20) as response:
                raw = response.read(128 * 1024 + 1)
        except HTTPError as error:
            status = error.code
            error.close()
            raise RuntimeError(f"ERA5 history returned HTTP {status}") from None
        except OSError:
            raise RuntimeError("ERA5 history connection failed") from None
        if len(raw) > 128 * 1024:
            raise ValueError("ERA5 history response exceeds the research size bound")
    days = daily_history(json.loads(raw), site, start, end)
    if not path.exists():
        write_atomic(path, raw)
    return {"days": days, "source_sha256": sha256(raw).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest, _ = load_targets(args.observations)
    window = json.loads(PROTOCOL_PATH.read_bytes())["fit_and_calibration"]
    start = latest_archive_day(date.fromisoformat(window["first_issue"])) - timedelta(
        days=LOOKBACK - 1
    )
    end = latest_archive_day(date.fromisoformat(window["last_issue"]))
    sites = [
        RootZoneSite(**{key: value for key, value in item.items() if key != "days"})
        for item in manifest["sites"]
        if item["days"] and item["role"] != "test"
    ]
    archive = {site.station_id: archive_station(site, start, end, args.cache) for site in sites}
    snapshot = {"source": OPEN_METEO_ARCHIVE, "sites": archive}
    write_atomically(args.output, json.dumps(snapshot, sort_keys=True, allow_nan=False) + "\n")
    print(f"Stored {sum(len(entry['days']) for entry in archive.values())} lagged ERA5 days")


if __name__ == "__main__":
    main()
