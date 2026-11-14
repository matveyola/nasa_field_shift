import argparse
import json
import ssl
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen

from pydantic import TypeAdapter

from field_shift.modules.rotations.historical_fields import (
    LOCAL_DATA_DIR,
    FieldConfig,
    FieldDataset,
    archive_url,
    build_dataset,
    bundled_field_files,
)

MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def latest_complete_year(today: date) -> int:
    # Leave one week for archive publication after the common May–September window.
    return today.year if today >= date(today.year, 10, 7) else today.year - 1


def ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    try:
        import certifi
    except ImportError:
        return context
    context.load_verify_locations(cafile=certifi.where())
    return context


def fetch_archive(url: str, context: ssl.SSLContext) -> dict:
    request = Request(
        url, headers={"User-Agent": "FieldShift historical field importer/1.0"}
    )
    with urlopen(request, timeout=45, context=context) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Archive response exceeds the expected size.")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise TypeError("Expected an archive response for one field.")
    return payload


def write_dataset(dataset: FieldDataset) -> Path:
    LOCAL_DATA_DIR.mkdir(exist_ok=True)
    destination = LOCAL_DATA_DIR / f"{dataset.id}.json"
    path = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".tmp",
            dir=LOCAL_DATA_DIR,
            delete=False,
        ) as temporary:
            path = Path(temporary.name)
            temporary.write(dataset.model_dump_json() + "\n")
        path.replace(destination)
    finally:
        if path is not None:
            path.unlink(missing_ok=True)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import historical weather for fields with a supplied soil profile."
    )
    parser.add_argument("config", type=Path, help="JSON list of field configurations")
    latest = latest_complete_year(datetime.now(UTC).date())
    parser.add_argument(
        "--end-year", type=int, default=latest, help="last complete growing season"
    )
    parser.add_argument(
        "--years", type=int, default=10, help="number of selectable seasons"
    )
    args = parser.parse_args()
    if not 3 <= args.years <= 30:
        parser.error("--years must be between 3 and 30")
    if not 1950 + args.years <= args.end_year <= latest:
        parser.error(f"the requested seasons must fall within 1950–{latest}")

    try:
        configs = TypeAdapter(list[FieldConfig]).validate_json(args.config.read_bytes())
        bundled_ids = {
            FieldDataset.model_validate_json(path.read_text(encoding="utf-8")).id
            for path in bundled_field_files()
        }
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if not configs:
        parser.error("provide at least one field configuration")
    if len({site.id for site in configs}) != len(configs):
        parser.error("field IDs must be unique")

    context = ssl_context()
    failed = False
    for site in configs:
        try:
            if site.id in bundled_ids:
                raise ValueError("This ID belongs to a bundled field. Choose a new ID.")
            # Both crop positions are evaluated on two successive seasons.
            url = archive_url(
                site.latitude, site.longitude, args.end_year - args.years, args.end_year
            )
            payload = fetch_archive(url, context)
            dataset = build_dataset(site, payload, url)
            expected_years = set(range(args.end_year - args.years, args.end_year + 1))
            if set(dataset.seasons) != expected_years:
                raise ValueError("The archive returned an incomplete set of years.")
            destination = write_dataset(dataset)
            print(
                f"{site.id}: imported {len(dataset.seasons)} complete seasons "
                f"into {destination}."
            )
        except (OSError, TypeError, ValueError) as error:
            print(f"{site.id}: import failed: {error}", file=sys.stderr)
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
