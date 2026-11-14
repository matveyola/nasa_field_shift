"""Compare lightweight root-zone models on geographically buffered development sites."""

import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from field_shift.modules.geo_ml.ifs import HORIZON
from field_shift.modules.geo_ml.moisture_model import station_distance_km
from field_shift.modules.simulation.root_zone_inputs import root_zone_storage_mm
from field_shift.modules.simulation.root_zone_model import (
    CAPACITY_MM,
    DEPTH_M,
    reference_reserve_mm,
    root_zone_features,
)
from field_shift.modules.simulation.water_balance import REFERENCE_WILTING_POINT
from field_shift_ml.fetch_observations import (
    USCRN,
    DownloadCache,
    Station,
    daily_observations,
    valid_moisture,
)
from field_shift_ml.fetch_root_zone_observations import (
    PROTOCOL_PATH,
    RootZoneSite,
    observations,
    select_sites,
)
from field_shift_ml.resources import evaluation_resource
from field_shift_ml.train_moisture import evaluate_predictions, write_atomically
from field_shift_ml.train_root_zone import Sample, assemble, fit, load_targets, weights

AUDIT_PATH = evaluation_resource("root-zone-extrapolation-sites.json")
BASES = ("fao56", "raw_ifs")
ALTERNATIVES = (
    "ifs_75_fao56_25",
    "ifs_50_fao56_50",
    "ifs_bias",
    "ifs_ridge",
    "ifs_ridge_local150",
    "ifs_ridge_local300",
    "fao56_ridge_v1",
)
WILTING_MM = 1000 * DEPTH_M * REFERENCE_WILTING_POINT
# Trapezoidal integration at 5, 10, 20, 50 and 100 cm, with the 5 cm value to the surface.
USCRN_DEPTH_WEIGHTS = (0.075, 0.075, 0.2, 0.4, 0.25)


def distance(left: RootZoneSite, right: RootZoneSite) -> float:
    return station_distance_km(left.latitude, left.longitude, right.latitude, right.longitude)


def fit_rows(samples: list[Sample], holdout: RootZoneSite, buffer_km: int) -> list[Sample]:
    """Exclude the validation site, its 2° block and any sites inside the buffer."""
    return [
        row
        for row in samples
        if row.site.block != holdout.block and distance(row.site, holdout) >= buffer_km
    ]


def raw_total(samples: list[Sample]) -> np.ndarray:
    return np.array([root_zone_storage_mm(row.day.moisture_layers, DEPTH_M) for row in samples])


def measured_total(samples: list[Sample], targets: dict) -> np.ndarray:
    return np.array([targets[row.site.station_id][row.day.date] for row in samples])


def compact_features(samples: list[Sample]) -> np.ndarray:
    return np.array(
        [
            (
                root_zone_storage_mm(row.day.moisture_layers, DEPTH_M),
                root_zone_storage_mm(row.run.initial_layers, DEPTH_M),
                row.day.soil_temperature_c,
            )
            for row in samples
        ]
    )


def spatial_taper(training: list[Sample], validation: list[Sample], radius_km: int) -> np.ndarray:
    fit_sites = {row.site for row in training}
    support = {
        site: max(0.0, 1 - min(distance(site, other) for other in fit_sites) / radius_km)
        for site in {row.site for row in validation}
    }
    return np.array([support[row.site] for row in validation])


def predictions(
    training: list[Sample], validation: list[Sample], targets: dict, names: tuple[str, ...]
) -> dict[str, tuple[np.ndarray, np.ndarray | None]]:
    """Predict available reserve and, for IFS-derived models, unclipped storage."""
    raw = raw_total(validation)
    physical = np.array([row.baseline for row in validation])
    output = {}
    ridge_correction = None
    for name in names:
        if name == "fao56":
            output[name] = physical, None
            continue
        if name == "fao56_ridge_v1":
            intercept, coefficients = fit(training)
            features = np.array(
                [root_zone_features(row.run, row.day, row.baseline) for row in validation]
            )
            output[name] = (
                np.clip(physical + intercept + features @ coefficients, 0, CAPACITY_MM),
                None,
            )
            continue
        if name == "raw_ifs":
            storage = raw
        elif name in ("ifs_75_fao56_25", "ifs_50_fao56_50"):
            fraction = 0.25 if name == "ifs_75_fao56_25" else 0.5
            storage = (1 - fraction) * raw + fraction * (WILTING_MM + physical)
        else:
            residual = measured_total(training, targets) - raw_total(training)
            station_weights = weights(training)
            if name == "ifs_bias":
                correction = np.average(residual, weights=station_weights)
            elif name in ("ifs_ridge", "ifs_ridge_local150", "ifs_ridge_local300"):
                if ridge_correction is None:
                    predictors = compact_features(training)
                    scaler = StandardScaler().fit(predictors, sample_weight=station_weights)
                    model = Ridge(alpha=100).fit(
                        scaler.transform(predictors), residual, sample_weight=station_weights
                    )
                    ridge_correction = model.predict(scaler.transform(compact_features(validation)))
                correction = ridge_correction
                if name != "ifs_ridge":
                    radius_km = 150 if name == "ifs_ridge_local150" else 300
                    correction = correction * spatial_taper(training, validation, radius_km)
            else:
                raise ValueError(f"Unknown candidate {name}")
            storage = raw + 0.5 * correction
        storage = np.clip(storage, 0, 1000 * DEPTH_M)
        output[name] = np.array([reference_reserve_mm(value) for value in storage]), storage
    return output


def score(
    samples: list[Sample], output: dict[str, tuple[np.ndarray, np.ndarray | None]], targets: dict
) -> dict:
    reserve = np.array([row.actual for row in samples])
    total = measured_total(samples, targets)
    station_weights = weights(samples)
    return {
        name: {
            "reserve_mm": evaluate_predictions(predicted, reserve, station_weights),
            "total_storage_mm": (
                evaluate_predictions(storage, total, station_weights)
                if storage is not None
                else None
            ),
        }
        for name, (predicted, storage) in output.items()
    }


def subset(output: dict, keep: np.ndarray) -> dict:
    return {
        name: (reserve[keep], storage[keep] if storage is not None else None)
        for name, (reserve, storage) in output.items()
    }


def grouped_score(samples: list[Sample], output: dict, targets: dict, key) -> dict:
    groups = np.array([key(row) for row in samples])
    return {
        str(group): score(
            [row for row in samples if key(row) == group], subset(output, groups == group), targets
        )
        for group in sorted(set(groups))
    }


def choose(overall: dict, groups: dict) -> tuple[str, list[str]]:
    raw = overall["raw_ifs"]
    metrics = ("mae", "rmse")
    eligible = [
        name
        for name in ALTERNATIVES
        if overall[name]["total_storage_mm"] is not None
        and all(
            overall[name]["reserve_mm"][metric] <= 0.95 * raw["reserve_mm"][metric]
            and overall[name]["total_storage_mm"][metric] <= raw["total_storage_mm"][metric]
            for metric in metrics
        )
        and all(
            site[name]["reserve_mm"]["rmse"] <= 1.10 * site["raw_ifs"]["reserve_mm"]["rmse"]
            for site in groups.values()
        )
    ]
    return min(
        eligible, key=lambda name: overall[name]["reserve_mm"]["rmse"], default="raw_ifs"
    ), eligible


def development(samples: list[Sample], targets: dict, buffer_km: int) -> dict:
    held_samples = []
    held_predictions = []
    sites = {row.site for row in samples}
    fold_sizes = {}
    for site in sorted(sites, key=lambda site: site.station_id):
        training = fit_rows(samples, site, buffer_km)
        validation = [row for row in samples if row.site == site]
        fit_sites = len({row.site for row in training})
        if fit_sites < 3:
            raise ValueError("At least three eligible fit sites are required for each fold")
        fold_sizes[site.station_id] = {"fit_sites": fit_sites, "pairs": len(validation)}
        held_samples.extend(validation)
        held_predictions.append(predictions(training, validation, targets, BASES + ALTERNATIVES))
    output = {
        name: (
            np.concatenate([part[name][0] for part in held_predictions]),
            np.concatenate([part[name][1] for part in held_predictions])
            if held_predictions[0][name][1] is not None
            else None,
        )
        for name in BASES + ALTERNATIVES
    }
    overall = score(held_samples, output, targets)
    sites = grouped_score(held_samples, output, targets, lambda row: row.site.station_id)
    leads = grouped_score(held_samples, output, targets, lambda row: row.day.lead_days)
    selected, eligible = choose(overall, sites | leads)
    return {
        "overall": overall,
        "by_site": sites,
        "by_lead": leads,
        "fold_sizes": fold_sizes,
        "selected": selected,
        "eligible_corrections": eligible,
        "pairs": len(held_samples),
        "capped_pairs": sum(row.actual in (0, CAPACITY_MM) for row in held_samples),
    }


def audit(
    cache_dir: Path,
    weather_cache: Path,
    prior: dict,
    training: list[Sample],
    calibration: list[Sample],
    targets: dict,
    selected: str,
) -> dict:
    protocol = AUDIT_PATH.read_bytes()
    plan = json.loads(protocol)
    cache = DownloadCache(cache_dir, False)
    sites, excluded = select_sites(cache, plan)
    previous, _ = select_sites(cache, prior)
    previous_ids = set(prior["fit"]) | set(prior["calibration"]) | set(prior["evaluation_farms"])
    if previous_ids & plan["evaluation_farms"].keys():
        raise ValueError("Extrapolation audit repeats a prior site")
    earlier_sites = {row.site for row in (*training, *calibration)} | {
        site for site in previous if site.role == "test"
    }
    if any(
        new.block == old.block or distance(new, old) < plan["spatial_buffer_km"]
        for old in earlier_sites
        for new in sites
    ):
        raise ValueError("Extrapolation audit overlaps a prior fit or holdout area")
    with ThreadPoolExecutor(max_workers=4) as pool:
        batches = list(pool.map(lambda site: observations(cache, site, plan), sites))
    observations_by_site = {site: batch for site, batch in zip(sites, batches, strict=True)}
    minimum = plan["minimum_holdout_pairs_per_farm"]
    viable = [site for site, batch in observations_by_site.items() if len(batch) >= minimum]
    audit_targets = {
        site.station_id: {date.fromisoformat(row["date"]): row["storage_mm"] for row in batch}
        for site, batch in observations_by_site.items()
    }
    rows = (
        assemble(viable, plan["evaluation"], audit_targets, weather_cache, plan["model_cycle"])
        if viable
        else []
    )
    counts = Counter(row.site.station_id for row in rows)
    qualified = [row for row in rows if counts[row.site.station_id] >= minimum]
    names = BASES if selected in BASES else (*BASES, selected)
    output = predictions(training, qualified, targets, names) if qualified else {}
    fit_sites = {row.site for row in training}
    return {
        "protocol_sha256": sha256(protocol).hexdigest(),
        "scan_source_sha256": sorted(set(cache.sources.values())),
        "excluded": excluded,
        "observations_by_site": {
            site: len(audit_targets.get(site, {})) for site in plan["evaluation_farms"]
        },
        "pairs_by_site": {site: counts[site] for site in plan["evaluation_farms"]},
        "qualified_sites": len({row.site for row in qualified}),
        "nearest_fit_km": {
            site.station_id: round(min(distance(site, other) for other in fit_sites), 1)
            for site in sites
        },
        "overall": score(qualified, output, audit_targets) if qualified else None,
        "by_site": grouped_score(qualified, output, audit_targets, lambda row: row.site.station_id),
        "by_lead": grouped_score(qualified, output, audit_targets, lambda row: row.day.lead_days),
    }


def uscrn_profiles(raw: str, station_id: str):
    for line in raw.splitlines():
        columns = line.lstrip("\x00").split()
        if not columns:
            continue
        if len(columns) != 38 or f"USCRN:{columns[0]}" != station_id:
            raise ValueError("Unexpected NOAA USCRN profile record")
        values = tuple(float(value) for value in columns[28:33])
        if all(valid_moisture(value) for value in values):
            stamp = datetime.strptime(columns[1] + columns[2], "%Y%m%d%H%M") - timedelta(hours=1)
            moisture = sum(
                value * depth for value, depth in zip(values, USCRN_DEPTH_WEIGHTS, strict=True)
            )
            yield stamp, moisture


def noaa_diagnostic(
    cache_dir: Path,
    weather_cache: Path,
    training: list[Sample],
    earlier_sites: set[RootZoneSite],
    targets: dict,
    selected: str,
) -> dict:
    plan = json.loads(AUDIT_PATH.read_bytes())
    metadata = plan["noaa_research_site"]
    window = plan["evaluation"]
    site = RootZoneSite(
        **{key: value for key, value in metadata.items() if key != "file"},
        utc_offset=0,
        role="test",
    )
    if any(
        site.block == earlier.block or distance(site, earlier) < plan["spatial_buffer_km"]
        for earlier in earlier_sites
    ):
        raise ValueError("NOAA diagnostic overlaps a prior development or holdout area")
    cache = DownloadCache(cache_dir, False)
    station = Station(site.station_id, site.latitude, site.longitude, "MO", "USCRN", site.name)
    first = date.fromisoformat(window["first_issue"]) + timedelta(days=1)
    last = date.fromisoformat(window["last_issue"]) + timedelta(days=HORIZON)
    url = f"{USCRN}/hourly02/{first.year}/{metadata['file']}"
    daily = daily_observations(
        station, uscrn_profiles(cache.read(url), site.station_id), first, last
    )
    labels = {site.station_id: {row.date: row.moisture * 1000 for row in daily}}
    minimum = plan["minimum_holdout_pairs_per_farm"]
    rows = (
        assemble([site], window, labels, weather_cache, plan["model_cycle"])
        if len(daily) >= minimum
        else []
    )
    qualified = len(rows) >= minimum
    names = BASES if selected in BASES else (*BASES, selected)
    output = predictions(training, rows, targets, names) if qualified else {}
    return {
        "site": metadata,
        "source_sha256": cache.sources[url],
        "observation_days": len(daily),
        "pairs": len(rows),
        "nearest_fit_km": round(min(distance(site, row.site) for row in training), 1),
        "managed_crop_depth_and_irrigation_verified": False,
        "overall": score(rows, output, labels) if qualified else None,
        "by_lead": grouped_score(rows, output, labels, lambda row: row.day.lead_days)
        if qualified
        else {},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--weather-cache", type=Path, required=True)
    parser.add_argument("--scan-cache", type=Path, required=True)
    parser.add_argument("--noaa-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(PROTOCOL_PATH.read_bytes())
    manifest, targets = load_targets(args.observations)
    earlier_sites = {
        RootZoneSite(**{key: value for key, value in item.items() if key != "days"})
        for item in manifest["sites"]
        if item["days"] or item["role"] == "test"
    }
    sites = sorted(
        (site for site in earlier_sites if site.role != "test"), key=lambda site: site.station_id
    )
    all_rows = assemble(
        sites, plan["fit_and_calibration"], targets, args.weather_cache, plan["model_cycle"]
    )
    training = [row for row in all_rows if row.site.role == "fit"]
    calibration = [row for row in all_rows if row.site.role == "calibration"]
    report = development(training, targets, plan["spatial_buffer_km"])
    selected = report["selected"]
    calibration_output = predictions(training, calibration, targets, BASES + ALTERNATIVES)
    result = {
        "schema_version": 1,
        "model_cycle": plan["model_cycle"],
        "development_observations_sha256": manifest["observations_sha256"],
        "development": report,
        "calibration": {
            "pairs": len(calibration),
            "overall": score(calibration, calibration_output, targets),
        },
        "extrapolation_audit": audit(
            args.scan_cache, args.weather_cache, plan, training, calibration, targets, selected
        ),
        "noaa_research_diagnostic": noaa_diagnostic(
            args.noaa_cache, args.weather_cache, training, earlier_sites, targets, selected
        ),
        "promoted": False,
    }
    write_atomically(
        args.output, json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    print(f"Development selected {selected}; serving artifact unchanged", flush=True)


if __name__ == "__main__":
    main()
