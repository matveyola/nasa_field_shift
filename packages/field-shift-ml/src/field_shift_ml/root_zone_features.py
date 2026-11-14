"""Water features available at the issue time of a pinned IFS run."""

from math import cos, log1p, pi, sin

import numpy as np

from field_shift.modules.crops.fao56 import crop_water_parameters
from field_shift.modules.simulation.root_zone_inputs import root_zone_storage_mm
from field_shift.modules.simulation.root_zone_model import (
    CAPACITY_MM,
    DEPTH_M,
    reference_reserve_mm,
)
from field_shift.modules.simulation.water_balance import fao56_days
from field_shift_ml.root_zone_history import HISTORY_FEATURES, history_features
from field_shift_ml.root_zone_soil import SOIL_FEATURES
from field_shift_ml.train_root_zone import Sample

FEATURES = {
    "core": ("initial_storage_mm", "lead_storage_mm", "shallow_soil_temperature_c"),
    "layers": (
        "initial_shallow_mid_gradient",
        "initial_mid_deep_gradient",
        "lead_shallow_mid_gradient",
        "lead_mid_deep_gradient",
        "shallow_layer_change",
        "middle_layer_change",
        "deep_layer_change",
    ),
    "weather": (
        "liquid_rain_mm",
        "et0_mm",
        "air_temperature_c",
        "air_soil_temperature_gap",
        "cumulative_rain_mm",
        "cumulative_et0_mm",
        "cumulative_net_weather_mm",
        "max_daily_log_rain",
        "wet_days",
        "dry_spell_days",
    ),
    "trajectory": (
        "lead_days",
        "storage_change_from_initial_mm",
        "one_day_storage_change_mm",
        "min_path_storage_mm",
        "max_path_storage_mm",
        "path_storage_variation_mm",
    ),
    "physics": (
        "fao56_reserve_mm",
        "fao56_water_use_mm",
        "cumulative_fao56_drainage_mm",
        "fao56_headroom_mm",
        "ifs_fao56_reserve_disagreement_mm",
        "fao56_potential_et_deficit_mm",
        "wetting_pressure_mm",
    ),
    "season": ("season_sin", "season_cos"),
    "geography": ("latitude", "longitude"),
    "soil": (*SOIL_FEATURES, "net_weather_fraction_of_available_capacity"),
    "history": HISTORY_FEATURES,
}


def sample_features(
    sample: Sample, soil: dict[str, float | None], history: dict | None = None
) -> dict[str, tuple]:
    run, day = sample.run, sample.day
    path = run.days[: day.lead_days]
    initial = root_zone_storage_mm(run.initial_layers, DEPTH_M)
    storage = [initial, *(root_zone_storage_mm(item.moisture_layers, DEPTH_M) for item in path)]
    initial_surface, initial_middle, initial_deep = run.initial_layers
    surface, middle, deep = day.moisture_layers
    rain = sum(item.rain_mm for item in path)
    et0 = sum(item.reference_et0_mm for item in path)
    dry_spell = next(
        (count for count, item in enumerate(reversed(path)) if item.rain_mm >= 1),
        len(path),
    )
    crop = crop_water_parameters("maize")
    assert crop is not None
    balance = fao56_days(
        path,
        initial_mm=reference_reserve_mm(initial),
        capacity_mm=CAPACITY_MM,
        threshold_mm=round((1 - crop.depletion_fraction) * CAPACITY_MM, 4),
        coefficient=crop.midseason_coefficient,
    )
    season_phase = 2 * pi * (day.date.timetuple().tm_yday - 1) / 365.2425
    site_awc = soil.get("available_capacity_mm")
    return {
        "core": (initial, storage[-1], day.soil_temperature_c),
        "layers": (
            initial_surface - initial_middle,
            initial_middle - initial_deep,
            surface - middle,
            middle - deep,
            surface - initial_surface,
            middle - initial_middle,
            deep - initial_deep,
        ),
        "weather": (
            day.rain_mm,
            day.reference_et0_mm,
            day.temperature_c,
            day.temperature_c - day.soil_temperature_c,
            rain,
            et0,
            rain - et0,
            log1p(max(item.rain_mm for item in path)),
            sum(item.rain_mm >= 1 for item in path),
            dry_spell,
        ),
        "trajectory": (
            day.lead_days,
            storage[-1] - initial,
            storage[-1] - storage[-2],
            min(storage),
            max(storage),
            sum(abs(right - left) for left, right in zip(storage[:-1], storage[1:], strict=True)),
        ),
        "physics": (
            sample.baseline,
            balance[-1].water_use_mm,
            sum(item.drainage_mm for item in balance),
            CAPACITY_MM - sample.baseline,
            sample.raw_ifs - sample.baseline,
            et0 * crop.midseason_coefficient - sum(item.water_use_mm for item in balance),
            max(0, day.rain_mm - (CAPACITY_MM - balance[-1].opening_mm)),
        ),
        "season": (sin(season_phase), cos(season_phase)),
        "geography": (sample.site.latitude, sample.site.longitude),
        "soil": (
            *(soil.get(name) for name in SOIL_FEATURES),
            (rain - et0) / site_awc if site_awc is not None and site_awc > 0 else None,
        ),
        "history": history_features(history or {}, run.initialized_on, initial),
    }


def feature_matrices(samples: list[Sample], soil: dict, history: dict) -> dict[str, np.ndarray]:
    rows = [
        sample_features(
            sample, soil.get(sample.site.station_id) or {}, history.get(sample.site.station_id)
        )
        for sample in samples
    ]
    return {group: np.array([row[group] for row in rows], dtype=float) for group in FEATURES}
