"""Randomised complete-block strips and descriptive, block-level harvest summaries."""

import csv
import io
from collections import defaultdict
from math import isfinite, sqrt
from random import Random
from statistics import mean, stdev

from shapely.affinity import rotate
from shapely.geometry import Point, box, shape
from shapely.ops import unary_union

from field_shift.modules.precision.geometry import feature, polygonal, project
from field_shift.modules.precision.schemas import TrialCreate


def design_trial(geometry: dict, crs: str, payload: TrialCreate) -> list[dict]:
    field = project(geometry, crs)
    interior = field.buffer(-payload.headland_m)
    if interior.is_empty:
        raise ValueError("The headland leaves no area for trial strips.")
    origin = (field.centroid.x, field.centroid.y)
    # A north-going strip is vertical at heading zero; clockwise compass headings.
    rotated = rotate(interior, payload.heading_degrees, origin=origin)
    left, bottom, right, top = rotated.bounds
    count = payload.replicates * len(payload.treatments)
    total_width = payload.strip_width_m * count
    if right - left < total_width:
        raise ValueError("The field is too narrow for this width, heading and replicate count.")
    left += (right - left - total_width) / 2
    rng = Random(payload.seed)
    strips = []
    mapped = []
    for block in range(payload.replicates):
        order = list(range(len(payload.treatments)))
        rng.shuffle(order)
        for position, treatment_id in enumerate(order):
            x = left + (block * len(order) + position) * payload.strip_width_m
            clipped = polygonal(
                rotated.intersection(box(x, bottom, x + payload.strip_width_m, top))
            )
            # Avoid tiny clipped remnants that cannot provide a usable replicate.
            if clipped.area < max(100, payload.strip_width_m * 10):
                raise ValueError("Some strips are too short; adjust the heading or headland.")
            actual = rotate(clipped, -payload.heading_degrees, origin=origin)
            treatment = payload.treatments[treatment_id]
            strips.append(
                feature(
                    actual,
                    crs,
                    zone=f"T{treatment_id + 1}",
                    treatment=treatment.name,
                    rate=treatment.rate,
                    block=block + 1,
                    plot_id=f"B{block + 1}-T{treatment_id + 1}",
                )
            )
            mapped.append(actual)
    remainder = polygonal(field.difference(unary_union(mapped)))
    if not remainder.is_empty:
        strips.append(
            feature(
                remainder,
                crs,
                zone="fallback",
                treatment=None,
                rate=payload.fallback_rate,
                block=None,
                plot_id=None,
            )
        )
    return strips


def parse_harvest(content: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    expected = {"longitude", "latitude", "yield_t_ha"}
    headers = reader.fieldnames or []
    if len(headers) != len(set(headers)) or not expected.issubset(headers):
        raise ValueError("Harvest CSV needs longitude,latitude,yield_t_ha and optional area_ha.")
    rows = []
    seen = set()
    has_area = "area_ha" in headers
    for index, raw in enumerate(reader, 2):
        if len(rows) >= 10_000:
            raise ValueError("Import at most 10,000 yield samples per report.")
        try:
            lon, lat, value = (float(raw[key]) for key in ("longitude", "latitude", "yield_t_ha"))
            weight = float(raw["area_ha"]) if has_area else 1.0
            if (
                None in raw
                or not all(isfinite(v) for v in (lon, lat, value, weight))
                or not -180 <= lon <= 180
                or not -90 <= lat <= 90
                or not 0 <= value <= 300
                or not 0 < weight <= 100
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError(f"Invalid yield sample on CSV row {index}.") from error
        if (lon, lat) in seen:
            raise ValueError(f"Duplicate yield coordinates on CSV row {index}.")
        seen.add((lon, lat))
        rows.append(
            {
                "longitude": lon,
                "latitude": lat,
                "yield_t_ha": value,
                "weight": weight,
                "area_weighted": has_area,
            }
        )
    if not rows:
        raise ValueError("The harvest file contains no samples.")
    return rows


def harvest_report(features: list[dict], rows: list[dict], crs: str) -> dict:
    plots = [
        (item, shape(item["geometry"])) for item in features if item["properties"].get("plot_id")
    ]
    samples = defaultdict(list)
    excluded = 0
    for row in rows:
        point = Point(row["longitude"], row["latitude"])
        matches = [item for item, polygon in plots if polygon.contains(point)]
        if len(matches) != 1:
            excluded += 1
            continue
        samples[matches[0]["properties"]["plot_id"]].append(row)
    summaries = []
    for item, _ in plots:
        props = item["properties"]
        values = samples[props["plot_id"]]
        total_weight = sum(row["weight"] for row in values)
        if values and values[0]["area_weighted"] and total_weight > props["area_ha"] * 1.05:
            raise ValueError(
                "Sampled area exceeds a trial plot; check overlapping harvest samples."
            )
        estimate = sum(row["yield_t_ha"] * row["weight"] for row in values)
        summaries.append(
            {
                **props,
                "count": len(values),
                "yield_t_ha": estimate / total_weight if values else None,
                "sampled_area_ha": total_weight if values and values[0]["area_weighted"] else None,
            }
        )
    treatments = list(dict.fromkeys(row["treatment"] for row in summaries))
    grouped = []
    baseline = treatments[0] if treatments else None
    # Stable treatment identity is T1 regardless of the randomised spatial ordering.
    for row in summaries:
        if row["zone"] == "T1":
            baseline = row["treatment"]
    baseline_by_block = {
        row["block"]: row["yield_t_ha"]
        for row in summaries
        if row["treatment"] == baseline and row["yield_t_ha"] is not None
    }
    for name in treatments:
        matching = [row for row in summaries if row["treatment"] == name]
        values = [row["yield_t_ha"] for row in matching if row["yield_t_ha"] is not None]
        differences = [
            row["yield_t_ha"] - baseline_by_block[row["block"]]
            for row in matching
            if row["yield_t_ha"] is not None and row["block"] in baseline_by_block
        ]
        grouped.append(
            {
                "treatment": name,
                "rate": matching[0]["rate"],
                "sample_count": sum(row["count"] for row in matching),
                "complete_blocks": len(values),
                "yield_t_ha": mean(values) if values else None,
                "paired_blocks": len(differences),
                "difference_t_ha": mean(differences) if differences else None,
                "difference_standard_error": stdev(differences) / sqrt(len(differences))
                if len(differences) >= 3 and name != baseline
                else None,
            }
        )
    return {
        "plots": summaries,
        "treatments": grouped,
        "baseline": baseline,
        "excluded_samples": excluded,
        "sample_count": len(rows),
        "weighting": "area_within_plot_equal_blocks"
        if rows[0]["area_weighted"]
        else "equal_samples_within_plot_equal_blocks",
        "interpretation": "Descriptive comparison of planned treatments; verify actual "
        "application, calibrated harvest data and independent replicated blocks.",
    }
