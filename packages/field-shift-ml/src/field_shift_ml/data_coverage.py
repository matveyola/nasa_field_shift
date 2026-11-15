"""Summarize published crop/territory metadata without inventing observed years or field truth."""

import argparse
import csv
import hashlib
import json
from pathlib import Path

APP_CROPS = ("Wheat", "Maize", "Soy", "Barley", "Sunflower", "Rapeseed", "Peas", "Rice")


def summarize(path):
    raw = path.read_bytes()
    if len(raw) > 1024 * 1024:
        raise ValueError("Coverage metadata exceeds 1 MiB")
    with path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    if not rows or len(rows) > 1000:
        raise ValueError("Expected 1–1000 published coverage rows")
    seen, cells = set(), []
    for row in rows:
        country, crop = row["country_code"], row["crop"]
        if (
            len(country) != 2
            or not country.isalpha()
            or country != country.upper()
            or crop not in APP_CROPS
        ):
            raise ValueError("Invalid country or application crop identifier")
        if (country, crop) in seen:
            raise ValueError("Duplicate country/crop")
        seen.add((country, crop))
        if row["truth_level"] != "administrative" or row["exact_years"] != "unknown":
            raise ValueError(
                "This source provides administrative statistics and year envelopes only"
            )
        count, regions = int(row["labels"]), int(row["admin_regions"])
        if min(count, regions) < 0:
            raise ValueError("Negative published counts")
        start = int(row["min_year"]) if row["min_year"] else None
        end = int(row["max_year"]) if row["max_year"] else None
        if count:
            if not regions or start is None or end is None or not 1900 <= start <= end <= 2200:
                raise ValueError("Nonempty source requires a valid year envelope and regions")
        elif start is not None or end is not None or regions:
            raise ValueError("Empty crop cell cannot assert observed years/regions")
        cells.append(
            {
                "country_code": country,
                "country": row["country"],
                "crop_id": crop,
                "source_country_code": row["source_country_code"],
                "administrative_level": row["admin_level"],
                "published_label_count": count,
                "published_region_count": regions,
                "min_year": start,
                "max_year": end,
                "exact_years": None,
                "truth_level": "administrative",
                "source_table": row["source_table"],
                "raw_archive_verified": False,
                "field_accuracy_validated": False,
            }
        )
    crops = []
    for crop in APP_CROPS:
        available = [r for r in cells if r["crop_id"] == crop and r["published_label_count"] > 0]
        crops.append(
            {
                "crop_id": crop,
                "published_countries": sorted(r["country_code"] for r in available),
                "published_country_count": len(available),
                "published_label_count": sum(r["published_label_count"] for r in available),
                "status": "published_metadata_not_scored"
                if available
                else "not_established_in_this_source",
                "validated_field_countries": [],
            }
        )
    return {
        "coverage_version": "crop-territory-evidence-v1",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source": {
            "name": "CY-Bench final-paper Tables 2–4, 2026-06-11",
            "url": "https://essd.copernicus.org/articles/18/3997/2026/",
            "metadata_kind": "transcribed_published_tables_not_archive_inventory",
            "latest_inspected_archive": {
                "version": "1.10",
                "record": "17279151",
                "url": "https://zenodo.org/records/17279151",
                "bytes": 6228749411,
                "publisher_md5": "8ec510982f396469eca25a4f0fa8632e",
                "downloaded": False,
                "licence": "EUPL-1.2 metadata; constituent-source terms require review",
            },
        },
        "country_union": sorted({r["country_code"] for r in cells}),
        "country_count": len({r["country_code"] for r in cells}),
        "crops": crops,
        "cells": cells,
        "limitations": [
            "Year ranges can have gaps; never expand into a complete yearly grid.",
            "A missing crop in this source is not proof of global data absence.",
            "Administrative yield statistics are not measured field/pixel yields.",
            "Publication metadata and a later archive release can differ.",
            "Coverage does not enable a model or establish geographic accuracy.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    text = json.dumps(summarize(args.metadata), indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
