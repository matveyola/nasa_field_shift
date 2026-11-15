"""Audit local ARDN/CSCAP source tables without fitting or claiming predictive accuracy.

Required --data-dir files: ardn-agro.csv, ardn-ops_plant.csv, ardn-ops_harv.csv,
ardn-weather_onsite.csv, ardn-sitemeta.csv, ardn-soil.csv, ardn-plotid.csv, cscap.xlsx.
CSV uses the source's cp1252 encoding. The parent workbook's sheet2 columns S/U/W
are its corn/soybean/wheat standard-moisture yields; ARDN grain_yield is checked
against their dry-matter conversion, with 0.11 kg/ha rounding tolerance. These
source-specific fractions are not observed harvest moisture or crop-model defaults.

The 120 cm/header diagnosis applies ONLY to the current Field Shift strict wrapper
and supplied defaults/input contracts. Native crop engines can use other documented
parameterizations; this audit does not run them or establish intrinsic engine limits.
No source data are downloaded or modified. Acquisition/permission receipts belong
to the operator, separately from the content hashes recorded here.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import zipfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

CROP_COLUMNS = {"corn": ("S", 0.155), "soybean": ("U", 0.13), "wheat": ("W", 0.135)}
OPERATIONS = {"corn": "corn", "soybean": "soy", "wheat": "wheat"}
ANNOTATIONS = {"description", "units"}
REQUIRED = {
    "ardn-agro.csv": {"uniqueid", "plotid", "year", "crop", "grain_yield", "grain_moisture"},
    "ardn-ops_plant.csv": {
        "uniqueid",
        "plotid",
        "cropyear",
        "cashcrop",
        "operation",
        "date",
        "planthybrid",
    },
    "ardn-ops_harv.csv": {"uniqueid", "plotid", "cropyear", "cashcrop", "operation", "date"},
    "ardn-weather_onsite.csv": {
        "station",
        "day",
        "highc",
        "lowc",
        "precipmm",
        "srad_onsite",
        "windspeed_onsite",
    },
    "ardn-sitemeta.csv": {"uniqueid", "state"},
    "ardn-soil.csv": {"uniqueid", "lowerbound", "SOIL02", "SOIL31", "SOIL32"},
    "ardn-plotid.csv": {"uniqueid", "plotid"},
}
COUNT_KEYS = (
    "grain_records",
    "unambiguous_sow_harvest",
    "same_year_ordered_calendar",
    "calendar_with_named_hybrid",
    "calendar_complete_temp_rain",
    "calendar_complete_temp_rain_srad_wind",
)


def finite(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def dry_yield_kg_ha(standard_value, crop):
    """Apply the documented parent-table standard basis, not measured grain moisture."""
    if crop not in CROP_COLUMNS or not finite(standard_value) or float(standard_value) < 0:
        raise ValueError("Known source crop and nonnegative finite standard yield required")
    return float(standard_value) * (1 - CROP_COLUMNS[crop][1])


def _read_csv(path, required):
    with path.open(encoding="cp1252", newline="") as source:
        reader = csv.DictReader(source)
        columns = reader.fieldnames or []
        if not required <= set(columns):
            raise ValueError(
                f"{path.name} missing columns: {', '.join(sorted(required - set(columns)))}"
            )
        if len(set(columns)) != len(columns):
            raise ValueError(f"Duplicate column names in {path.name}")
        rows, excluded = [], 0
        for row in reader:
            if None in row or any(row[key] is None for key in required):
                raise ValueError(f"Malformed CSV row in {path.name}")
            if row.get("uniqueid", row.get("station")) in ANNOTATIONS:
                excluded += 1
            else:
                rows.append(row)
    return rows, columns, excluded


def _workbook_rows(path):
    with zipfile.ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = [
                "".join(r.itertext()).strip()
                for r in ET.fromstring(archive.read("xl/sharedStrings.xml"))
            ]
        if "xl/worksheets/sheet2.xml" not in archive.namelist():
            raise ValueError("cscap.xlsx requires the parent yield worksheet sheet2.xml")
        result = {}
        with archive.open("xl/worksheets/sheet2.xml") as sheet:
            for _, element in ET.iterparse(sheet, events=("end",)):
                if not element.tag.endswith("}row"):
                    continue
                row = {}
                for cell in element:
                    column = "".join(x for x in cell.attrib.get("r", "") if x.isalpha())
                    value = cell.find("{*}v")
                    if cell.get("t") == "inlineStr":
                        inline = cell.find("{*}is")
                        row[column] = "".join(inline.itertext()) if inline is not None else ""
                    elif value is not None and value.text is not None:
                        if cell.get("t") == "s":
                            index = int(value.text)
                            if not 0 <= index < len(strings):
                                raise ValueError("Invalid shared-string index in cscap.xlsx")
                            row[column] = strings[index]
                        else:
                            row[column] = value.text
                element.clear()
                if row.get("C", "").isdigit():
                    key = (row.get("A"), row.get("B"), row["C"])
                    if key in result and result[key] != row:
                        raise ValueError(f"Ambiguous parent-workbook site/plot/year key: {key}")
                    result[key] = row
    return result


def _calendar_summary(agro, plant, harvest, weather):
    byplant, byharvest = defaultdict(list), defaultdict(list)
    for records, grouped in ((plant, byplant), (harvest, byharvest)):
        for row in records:
            if row["cropyear"].isdigit():
                grouped[row["uniqueid"], row["plotid"], row["cropyear"], row["cashcrop"]].append(
                    row
                )
    byweather = defaultdict(dict)
    for row in weather:
        day = date.fromisoformat(row["day"].replace("/", "-"))
        previous = byweather[row["station"]].get(day)
        if previous is not None and previous != row:
            raise ValueError("Conflicting weather rows for the same station/day")
        byweather[row["station"]][day] = row
    summary, examples = defaultdict(Counter), {}
    for row in agro:
        crop = row["crop"]
        if crop not in OPERATIONS:
            raise ValueError(f"Unknown crop in this source schema: {crop!r}")
        summary[crop]["grain_records"] += 1
        key = (row["uniqueid"], row["plotid"], row["year"], crop)
        pp = [p for p in byplant[key] if p["operation"] == "plant_" + OPERATIONS[crop]]
        hh = [h for h in byharvest[key] if h["operation"] == "harvest_" + OPERATIONS[crop]]
        ps = {p["date"] for p in pp if p["date"] not in ("NA", "")}
        hs = {h["date"] for h in hh if h["date"] not in ("NA", "")}
        if len(ps) != 1 or len(hs) != 1:
            continue
        summary[crop]["unambiguous_sow_harvest"] += 1
        start, end = date.fromisoformat(next(iter(ps))), date.fromisoformat(next(iter(hs)))
        if start.year == end.year == int(row["year"]) and start < end:
            summary[crop]["same_year_ordered_calendar"] += 1
        if any(p["planthybrid"] not in ("NA", "") for p in pp):
            summary[crop]["calendar_with_named_hybrid"] += 1
        daily = [r for d, r in byweather[row["uniqueid"]].items() if start <= d <= end]
        complete = (
            start <= end
            and len(daily) == (end - start).days + 1
            and all(
                all(finite(day[field]) for field in ("highc", "lowc", "precipmm")) for day in daily
            )
        )
        if complete:
            summary[crop]["calendar_complete_temp_rain"] += 1
        if complete and all(
            all(finite(d[f]) for f in ("srad_onsite", "windspeed_onsite")) for d in daily
        ):
            summary[crop]["calendar_complete_temp_rain_srad_wind"] += 1
        if crop not in examples:
            examples[crop] = {
                k: row[k] for k in ("uniqueid", "plotid", "year", "grain_yield", "grain_moisture")
            } | {"sow": start.isoformat(), "harvest": end.isoformat()}
    return {
        crop: {key: counts.get(key, 0) for key in COUNT_KEYS}
        for crop, counts in sorted(summary.items())
    }, examples


def audit(data_dir: Path):
    """Summarize local source availability and basis consistency, never engine accuracy."""
    data_dir = data_dir.resolve()
    for name in (*REQUIRED, "cscap.xlsx"):
        if not (data_dir / name).is_file():
            raise ValueError(f"Required input missing: {name}")
    tables, columns, annotations, hashes = {}, {}, {}, {}
    for name, required in REQUIRED.items():
        tables[name], columns[name], annotations[name] = _read_csv(data_dir / name, required)
    for name in (*REQUIRED, "cscap.xlsx"):
        with (data_dir / name).open("rb") as source:
            hashes[name] = hashlib.file_digest(source, "sha256").hexdigest()
    agro = [r for r in tables["ardn-agro.csv"] if r["year"].isdigit() and finite(r["grain_yield"])]
    summary, examples = _calendar_summary(
        agro,
        tables["ardn-ops_plant.csv"],
        tables["ardn-ops_harv.csv"],
        tables["ardn-weather_onsite.csv"],
    )
    original = _workbook_rows(data_dir / "cscap.xlsx")
    checks, errors = Counter(), []
    for row in agro:
        column, _ = CROP_COLUMNS[row["crop"]]
        source = original.get((row["uniqueid"], row["plotid"], row["year"]))
        if source is not None and finite(source.get(column)):
            expected = dry_yield_kg_ha(source[column], row["crop"])
            error = abs(float(row["grain_yield"]) - expected)
            checks[row["crop"]] += 1
            if error > 0.11:
                errors.append(
                    {
                        "site": row["uniqueid"],
                        "plot": row["plotid"],
                        "year": row["year"],
                        "crop": row["crop"],
                        "difference_kg_ha": error,
                    }
                )
    site, soil = tables["ardn-sitemeta.csv"], tables["ardn-soil.csv"]
    depths = [float(r["lowerbound"]) for r in soil if finite(r["lowerbound"])]
    return {
        "schema_version": "public-crop-data-audit-v1",
        "purpose": "Raw ARDN/CSCAP source schema/readiness audit, "
        "not a predictive accuracy benchmark",
        "input_sha256": hashes,
        "grain_records_by_crop": summary,
        "years": sorted({int(r["year"]) for r in agro}),
        "yield_site_codes": sorted({r["uniqueid"] for r in agro}),
        "yield_sites": len({r["uniqueid"] for r in agro}),
        "known_state_codes": sorted({r["state"] for r in site if r["state"] not in ("NA", "")}),
        "unknown_state_rows": sum(r["state"] in ("NA", "") for r in site),
        "metadata_annotation_rows_excluded": annotations["ardn-sitemeta.csv"],
        "annotation_rows_excluded_by_file": annotations,
        "independence_status": "Experiment/site codes, NOT necessarily independent farms; "
        "no untouched-holdout claim",
        "potential_shared_farm_groups_requiring_crosswalk": [
            ["ISUAG", "ISUAG.USB"],
            ["WOOSTER.COV", "WOOSTER.LTR"],
        ],
        "dry_basis_crosscheck_against_parent_standard_moisture": {
            "matched_rows": dict(sorted(checks.items())),
            "differences_over_0_11kg_ha": len(errors),
            "examples": errors[:5],
            "source_standard_moisture_fractions": {c: v[1] for c, v in CROP_COLUMNS.items()},
            "unit": "kg/ha",
            "method": "parent standard yield * (1 - source standard moisture fraction)",
        },
        "soil_measured_max_depth_cm": max(depths) if depths else None,
        "soil_hydraulic_complete_rows": sum(
            all(finite(r[c]) for c in ("SOIL02", "SOIL31", "SOIL32")) for r in soil
        ),
        "soil_hydraulic_completeness_method": "Finite source SOIL02/SOIL31/SOIL32 cells, "
        "not validated engine parameterization",
        "weather_columns": columns["ardn-weather_onsite.csv"],
        "soil_columns": columns["ardn-soil.csv"],
        "full_strict_engine_ready_records": 0,
        "readiness_scope": "Current Field Shift strict wrapper/default profiles only, "
        "NOT an intrinsic native-engine requirement",
        "readiness_count_method": "No engine validations executed. Current wrapper inputs lack "
        "explicit field opening water, hydraulic/management/availability provenance and cultivar "
        "mapping. Source headers and measured depth are structural diagnostics, not proof that "
        "native AquaCrop/WOFOST cannot use other parameterizations.",
        "missing_required_inputs": [
            "measured initial root/full-profile water for current wrapper",
            "Ksat and penetrability parameterization for current wrapper",
            "Deeper or reviewed root-profile parameterization for current "
            "Maize/Wheat wrapper defaults",
            "ET0 or humidity/vapor pressure to compute reference ET for current wrapper",
            "snow/frozen-soil state for current wrapper",
            "input availability/forecast issue-cycle-lead history",
            "validated ready-engine cultivar mapping",
            "plot geometry crosswalk (site corners are not plot polygons)",
        ],
        "sample_calendar_keys": examples,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        protected = {(args.data_dir / name).resolve() for name in (*REQUIRED, "cscap.xlsx")}
        if args.output.resolve() in protected:
            raise ValueError("Audit output must not overwrite a source input")
        result = audit(args.data_dir)
        args.output.write_text(
            json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
    except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError) as error:
        parser.error(str(error))
    print(
        f"Audited {sum(v['grain_records'] for v in result['grain_records_by_crop'].values())} "
        f"grain records across {result['yield_sites']} experiment/site codes; "
        "no predictive accuracy claim."
    )


if __name__ == "__main__":
    main()
