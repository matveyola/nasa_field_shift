"""Compare global, explicitly parameterized continuous water scenarios.

This offline command closes mixed-calendar/snow accounting gaps for experiments.
It does not promote a crop-yield model or bypass the application's release gates.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from field_shift_science.layered_water.planting import CalendarParameters
from field_shift_science.layered_water.runner import LayeredParameters, run_water_balance
from field_shift_science.layered_water.weather import WeatherDay

from .provenance import digest

CROPS = ("Maize", "Wheat", "Soy", "Barley", "Sunflower", "Rapeseed", "Peas", "Rice")


def parse_calendar(raw):
    values = dict(raw)
    values["sowing"] = date.fromisoformat(values["sowing"])
    values["harvest"] = date.fromisoformat(values["harvest"])
    values["dormancy"] = tuple(
        (date.fromisoformat(a), date.fromisoformat(b)) for a, b in values["dormancy"]
    )
    values["stage_lengths"] = tuple(values["stage_lengths"])
    if values["crop_id"] not in CROPS:
        raise ValueError("Unknown crop; no cross-species parameter substitution")
    return CalendarParameters(**values)


def compare(document):
    issue = datetime.fromisoformat(document["as_of"].replace("Z", "+00:00"))
    if issue.tzinfo is None:
        raise ValueError("Explicit issue timezone required")
    location = document["location"]
    lat, lon = location["latitude"], location["longitude"]
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("Invalid coordinates")
    region = document["parameter_region"]
    west, south, east, north = region["bounds"]
    inside_lon = west <= lon <= east if west <= east else lon >= west or lon <= east
    if not -180 <= west <= 180 or not -180 <= east <= 180 or not -90 <= south <= north <= 90:
        raise ValueError("Invalid declared parameter footprint")
    if not inside_lon or not south <= lat <= north:
        raise ValueError("Location outside declared parameter footprint; no global fallback")
    if not region["source"].strip() or not region["region_id"].strip():
        raise ValueError("Parameter region provenance required")
    if region.get("validated") is not False:
        raise ValueError("This experimental adapter has no validated regional scope")
    parameters = LayeredParameters(**document["parameters"])
    alternatives = document["alternatives"]
    scenarios = document["scenarios"]
    if not 1 <= len(alternatives) <= 3 or not 1 <= len(scenarios) <= 8:
        raise ValueError("At most three alternatives and eight scenarios")
    if len({a["id"] for a in alternatives}) != len(alternatives):
        raise ValueError("Duplicate alternatives")
    if len({s["id"] for s in scenarios}) != len(scenarios):
        raise ValueError("Duplicate scenarios")
    members = []
    expected_dates = None
    for scenario in scenarios:
        available = datetime.fromisoformat(scenario["available_at"].replace("Z", "+00:00"))
        if available.tzinfo is None or available > issue:
            raise ValueError("Scenario source unavailable at issue time")
        if scenario["kind"] not in {"historical_analogue", "synthetic"}:
            raise ValueError("Only labeled analogue or synthetic scenarios; no weather oracle")
        if (
            scenario["kind"] == "historical_analogue"
            and scenario.get("stationary_climate_assumption") is not True
        ):
            raise ValueError("Analogue experiment must declare stationary-climate assumption")
        wx = []
        source_dates = []
        for raw in scenario["weather"]:
            values = {k: v for k, v in raw.items() if k != "source_date"}
            values["date"] = date.fromisoformat(values["date"])
            if values["date"] <= issue.date():
                raise ValueError("Planning window must be future")
            if scenario["kind"] == "historical_analogue":
                original = date.fromisoformat(raw["source_date"])
                if original >= issue.date():
                    raise ValueError("Future observations cannot enter scenarios")
                source_dates.append(original)
            wx.append(WeatherDay(**values))
        if any((b - a).days != 1 for a, b in zip(source_dates, source_dates[1:])):
            raise ValueError("Analogue members must preserve complete chronological weather")
        if source_dates:
            complete_at = datetime.combine(
                source_dates[-1] + timedelta(days=1), time.min, tzinfo=UTC
            )
            if available < complete_at:
                raise ValueError("Source availability precedes its completed observations")
        dates = [d.date for d in wx]
        if expected_dates is None:
            expected_dates = dates
        elif expected_dates != dates:
            raise ValueError("All scenarios must cover the same daily window")
        members.append((scenario, wx))
    result = []
    for alternative in alternatives:
        calendars = [parse_calendar(c) for c in alternative["calendars"]]
        rows = []
        try:
            for scenario, weather in members:
                irrigation = {
                    date.fromisoformat(k): v for k, v in alternative["irrigation_mm"].items()
                }
                run = run_water_balance(
                    parameters,
                    weather,
                    calendars,
                    irrigation=irrigation,
                    land_status=document["land_status"],
                )
                active = [d for d in run.days if d.cover != "fallow" and d.stage != "dormant"]
                etc = sum(d.etc_mm for d in active)
                deficit = sum(max(0.0, d.etc_mm - d.eta_mm) for d in active)
                rows.append(
                    {
                        "scenario_id": scenario["id"],
                        "unmet_fraction": deficit / etc if etc > 0 else None,
                        "deficit_mm": deficit,
                        "stress_days": sum(d.stress for d in active),
                        "irrigation_mm": sum(d.irrigation_mm for d in run.days),
                        "max_balance_error_mm": run.max_balance_error_mm,
                        "closing_profile_reserve_mm": run.closing_profile_mm,
                        "closing_snow_mm": run.closing_snow_mm,
                        "input_sha256": digest(
                            {
                                "parameters": asdict(parameters),
                                "calendar": [asdict(c) for c in calendars],
                                "weather": [asdict(w) for w in weather],
                                "irrigation": alternative["irrigation_mm"],
                            }
                        ),
                        "sources": run.sources,
                    }
                )
            fractions = [r["unmet_fraction"] for r in rows]
            score = max(fractions) if all(v is not None for v in fractions) else None
            result.append(
                {
                    "id": alternative["id"],
                    "status": "experimental_water_only",
                    "members": rows,
                    "worst_tested_unmet_fraction": score,
                    "yield_t_ha": None,
                    "gross_margin": None,
                    "pros": ["Quantified water demand and deficit on the supplied scenarios"],
                    "cons": [
                        "No locally validated yield, survival, nitrogen, flood "
                        "or economic response",
                        "Calendar/root/Kc/drainage choices materially influence rankings",
                    ],
                }
            )
        except ValueError as error:
            result.append(
                {
                    "id": alternative["id"],
                    "status": "unsupported",
                    "reason": str(error),
                    "members": [],
                    "worst_tested_unmet_fraction": None,
                    "yield_t_ha": None,
                    "gross_margin": None,
                }
            )
    ranked = sorted(
        (r for r in result if r["worst_tested_unmet_fraction"] is not None),
        key=lambda r: (r["worst_tested_unmet_fraction"], r["id"]),
    )
    return {
        "schema_version": "global-water-experiment-v1",
        "promotion": False,
        "validated": False,
        "target": "crop_water_deficit",
        "units": "mm and dimensionless unmet demand fraction",
        "location": location,
        "parameter_region": region,
        "as_of": issue.isoformat(),
        "input_sha256": digest(document),
        "model": "team-layered-water-regional-calendar-v1",
        "ranking_by_worst_tested_unmet_fraction": [r["id"] for r in ranked],
        "alternatives": result,
        "limitations": [
            "Scenario frequencies are not calibrated probabilities",
            "Water-deficit ordering is not a profit or agronomic recommendation",
            "No location is scientifically validated by a passing software test",
            "Total precipitation must include snowfall liquid equivalent; "
            "do not pass liquid-only rain",
            "Opening-water stress, degree-day snow and group-specific runoff "
            "remain declared approximations",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("Output must not overwrite input")
    if args.input.stat().st_size > 16 * 1024**2:
        parser.error("Input exceeds16MiB")
    result = compare(json.loads(args.input.read_text()))
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False, default=str) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
