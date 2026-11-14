"""Reproduce geographical execution cases on pinned public NASA POWER forcing.

Weather is real coarse-grid input. Soil, crop-phase and management values here are
explicit controlled assumptions. These cases never measure crop-yield accuracy.
City names identify representative climate coordinates, not verified farm parcels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from field_shift_science.layered_water.planting import CalendarParameters
from field_shift_science.layered_water.power import parse_power
from field_shift_science.layered_water.runner import LayeredParameters

from .global_water import compare
from .provenance import digest

LOCATIONS = {
    "kyiv": (50.45, 30.52, 4, 1, "arable"),
    "rio": (-22.91, -43.17, 10, 1, "arable"),
    "singapore": (1.35, 103.82, 4, 1, "arable"),
    "sahara": (23.0, 13.0, 4, 1, "unknown"),
    "antarctica": (-80.0, 0.0, 11, 1, "ice"),
    "la_paz": (-16.5, -68.15, 4, 1, "unknown"),
}
CROPS = ("Maize", "Wheat", "Soy", "Barley", "Sunflower", "Rapeseed", "Peas", "Rice")


def benchmark(data_dir: Path):
    outputs = []
    for name, (latitude, longitude, month, day, land) in LOCATIONS.items():
        path = data_dir / f"global-{name}-power-2023-2024.json"
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        document = json.loads(raw)
        longitude_actual, latitude_actual, _ = document["geometry"]["coordinates"]
        if abs(latitude_actual - latitude) > 1e-6 or abs(longitude_actual - longitude) > 1e-6:
            raise ValueError("NASA panel coordinate mismatch")
        parsed = {w.date: w for w in parse_power(document, source_checksum=sha)}
        sow = date(2027, month, day)
        harvest = sow + timedelta(days=119)
        original = date(2023, month, day)
        weather = []
        for i in range(120):
            source = parsed[original + timedelta(days=i)]
            weather.append(
                asdict(source)
                | {
                    "date": (sow + timedelta(days=i)).isoformat(),
                    "source_date": source.date.isoformat(),
                }
            )
        parameters = LayeredParameters(
            field_capacity=(0.30,) * 15,
            wilting_point=(0.15,) * 15,
            saturation=(0.45,) * 15,
            initial_vwc=(0.25,) * 15,
            drainage_fraction=(0.1,) * 15,
            curve_numbers=dict(row=78.0, small_grain=75.0, residue=83.0, bare=86.0),
            snow_temperature_c=0.0,
            melt_mm_per_c_day=3.0,
            freeze_tmean_7d_c=-1.0,
            frozen_curve_number=95.0,
            initial_residue_cover=0.3,
            initial_snow_mm=0.0,
            prior_tmean_c=tuple(
                (
                    parsed[original - timedelta(days=k)].tmax
                    + parsed[original - timedelta(days=k)].tmin
                )
                / 2
                for k in range(7, 0, -1)
            ),
            source="Controlled example hydraulics/CN/snow assumptions, not measured regional soil",
        )
        alternatives = []
        for crop in CROPS:
            c = CalendarParameters(
                crop,
                sow,
                harvest,
                (20, 30, 40, 30),
                0.3,
                1.15,
                0.35,
                0.15,
                1.0,
                0.5,
                1.5,
                0.3,
                "row",
                (),
                "Controlled equal-phase crop parameters; NOT calibrated local phenology",
            )
            alternatives.append({"id": crop, "calendars": [asdict(c)], "irrigation_mm": {}})
        # Each bounded comparison carries at most three alternatives; no benefit from
        # hiding unsupported/missing-context crops in the full eight-crop inventory.
        batches = []
        for offset in range(0, len(alternatives), 3):
            request = {
                "as_of": "2026-11-14T00:00:00Z",
                "location": {"latitude": latitude, "longitude": longitude},
                "parameter_region": {
                    "region_id": "controlled-" + name,
                    "bounds": [
                        max(-180.0, longitude - 1),
                        max(-90.0, latitude - 1),
                        min(180.0, longitude + 1),
                        min(90.0, latitude + 1),
                    ],
                    "source": "Execution-case footprint only, not validation geography",
                    "validated": False,
                },
                "parameters": asdict(parameters),
                "land_status": land,
                "alternatives": alternatives[offset : offset + 3],
                "scenarios": [
                    {
                        "id": "NASA-2023-replay",
                        "kind": "historical_analogue",
                        "stationary_climate_assumption": True,
                        "available_at": "2026-11-14T00:00:00Z",
                        "weather": weather,
                    }
                ],
            }
            request = json.loads(json.dumps(request, default=str))
            batches.append(compare(request))
        outcomes = [a for batch in batches for a in batch["alternatives"]]
        outputs.append(
            {
                "location": name,
                "representative_coordinates": [latitude, longitude],
                "land_status": land,
                "land_status_basis": "controlled case assumption; no farm-parcel classification",
                "raw_source_sha256": sha,
                "source_dates": [
                    original.isoformat(),
                    (original + timedelta(days=119)).isoformat(),
                ],
                "target_dates": [sow.isoformat(), harvest.isoformat()],
                "mean_tmin_c": sum(row["tmin"] for row in weather) / 120,
                "total_precipitation_mm": sum(row["rain_mm"] for row in weather),
                "derived_fao56_et0_mm": sum(row["et0_mm"] for row in weather),
                "outcomes": outcomes,
            }
        )
    return {
        "schema_version": "global-execution-cases-v1",
        "promotion": False,
        "empirical_accuracy": "not_measured",
        "crop_catalog": list(CROPS),
        "locations": outputs,
        "source_set_sha256": digest([r["raw_source_sha256"] for r in outputs]),
        "limitations": [
            "Actual historical weather with controlled unvalidated soil/crop assumptions",
            "No city-coordinate point establishes cropland, field suitability or model accuracy",
            "Sahara/high-altitude cases abstain without confirmed arable land",
            "Antarctic ice abstains for every crop; paddy rice always unsupported",
            "Identical crop parameters isolate climate effects and cannot rank species",
            "2023 weather replay is one uncalibrated scenario, not a forecast of 2027 conditions",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = {
        (args.data_dir / f"global-{name}-power-2023-2024.json").resolve() for name in LOCATIONS
    }
    if args.output.resolve() in inputs:
        parser.error("Output must not overwrite a source weather response")
    result = benchmark(args.data_dir)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
