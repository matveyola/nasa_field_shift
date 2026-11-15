from dataclasses import dataclass
from typing import Literal

from field_shift.modules.fields.geometry import contains_point
from field_shift.modules.fields.schemas import Polygon
from field_shift.modules.simulation.root_zone_inputs import root_zone_storage_mm
from field_shift.modules.simulation.water_balance import (
    REFERENCE_FIELD_CAPACITY,
    REFERENCE_WILTING_POINT,
)

# SoilGrids wv0033 and wv1500 at the Marsden Farm demo point. Depths in metres.
MARSDEN_POINT = (-93.78421, 42.01379)
MARSDEN_LAYERS = (
    (0.00, 0.05, 0.357, 0.224),
    (0.05, 0.15, 0.361, 0.223),
    (0.15, 0.30, 0.360, 0.219),
    (0.30, 0.60, 0.348, 0.210),
    (0.60, 1.00, 0.332, 0.206),
)
IFS_DEPTHS = ((0.00, 0.07), (0.07, 0.28), (0.28, 1.00))


@dataclass(frozen=True, slots=True)
class ForecastSoil:
    source: Literal["reference_loam", "soilgrids_marsden"]
    field_capacity: float
    wilting_point: float
    capacity_mm: float
    initial_reserve_mm: float


def forecast_soil(
    geometry: Polygon, moisture: tuple[float, float, float], depth_m: float
) -> ForecastSoil:
    if contains_point(geometry.coordinates[0], *MARSDEN_POINT):
        return marsden_soil(moisture, depth_m)
    raw_storage = root_zone_storage_mm(moisture, depth_m)
    fc, wp = REFERENCE_FIELD_CAPACITY, REFERENCE_WILTING_POINT
    capacity = 1000 * (fc - wp) * depth_m
    return ForecastSoil(
        "reference_loam",
        fc,
        wp,
        round(capacity, 4),
        round(min(capacity, max(0.0, raw_storage - 1000 * wp * depth_m)), 4),
    )


def marsden_soil(moisture: tuple[float, float, float], depth_m: float) -> ForecastSoil:
    return soil_water_from_layers(moisture, depth_m, MARSDEN_LAYERS)


def soil_water_from_layers(
    moisture: tuple[float, float, float],
    depth_m: float,
    layers: tuple[tuple[float, float, float, float], ...],
) -> ForecastSoil:
    if not 0 < depth_m <= 1 or not all(0 <= theta <= 1 for theta in moisture):
        raise ValueError(
            "Soil water requires three moisture values and a depth up to 1 m."
        )
    fc_depth = wp_depth = available_depth = 0.0
    for top, bottom, fc, wp in layers:
        thickness = max(0.0, min(depth_m, bottom) - top)
        fc_depth += fc * thickness
        wp_depth += wp * thickness
        for (ifs_top, ifs_bottom), theta in zip(IFS_DEPTHS, moisture, strict=True):
            overlap = max(0.0, min(depth_m, bottom, ifs_bottom) - max(top, ifs_top))
            available_depth += overlap * max(0.0, min(fc, theta) - wp)

    return ForecastSoil(
        "soilgrids_marsden",
        fc_depth / depth_m,
        wp_depth / depth_m,
        round(1000 * (fc_depth - wp_depth), 4),
        round(1000 * available_depth, 4),
    )
