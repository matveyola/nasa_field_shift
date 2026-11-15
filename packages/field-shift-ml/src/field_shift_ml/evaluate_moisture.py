"""Score a frozen model on a separate set of never-seen field sensors."""

import argparse
import json
from datetime import date
from hashlib import sha256
from pathlib import Path

from field_shift_science.geo_ml.moisture_model import MoistureModel, station_distance_km

from field_shift_ml.train_moisture import (
    benchmark,
    join_stations,
    read_observations,
    write_atomically,
)

_MINIMUM_DISTANCE_KM = 40


def evaluate(model: MoistureModel, observations: Path, cache: Path) -> dict:
    validation = model.validation
    start = date.fromisoformat(validation["evaluation_start"])
    end = date.fromisoformat(validation["evaluation_end"])
    samples = [
        sample
        for sample in join_stations(read_observations(observations), cache)
        if start <= sample.day.date <= end and model.supports_date(sample.day.date)
    ]
    locations = validation["station_splits"]["train"] + validation["station_splits"]["calibration"]
    used_ids = {location["station_id"] for location in locations}
    sites = []
    for station in sorted({sample.station for sample in samples}, key=lambda item: item.id):
        distance = min(
            station_distance_km(
                station.latitude, station.longitude, location["latitude"], location["longitude"]
            )
            for location in locations
        )
        if station.id in used_ids or distance < _MINIMUM_DISTANCE_KM:
            raise ValueError(f"{station.id} overlaps a training or calibration location")
        sites.append(
            {
                "station_id": station.id,
                "location": station.location,
                "latitude": station.latitude,
                "longitude": station.longitude,
                "minimum_fit_or_calibration_distance_km": distance,
                "shares_fit_or_calibration_block": any(
                    station.block == location["block"] for location in locations
                ),
                **benchmark(model, [sample for sample in samples if sample.station == station]),
            }
        )
    if not sites:
        raise ValueError("No complete independent observations in the model evaluation period")
    return {
        "model_version": model.version,
        "observation_csv_sha256": sha256(observations.read_bytes()).hexdigest(),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "summary": benchmark(model, samples),
        "sites": sites,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    model = MoistureModel.from_json(args.model.read_bytes())
    report = evaluate(model, args.observations, args.cache)
    write_atomically(args.output, json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
