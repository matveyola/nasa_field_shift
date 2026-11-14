"""Estimate daily root-zone wetness with a finite, free-drainage bucket."""

from collections.abc import Mapping, Sequence
from datetime import date
from functools import lru_cache
from importlib.resources import files
from json import loads
from math import expm1, fsum
from typing import Literal

from pydantic import BaseModel

from field_shift.modules.rotations.historical_fields import WeatherDay
from field_shift.modules.rotations.waterlogging_inputs import SoilWaterloggingProfile
from field_shift.modules.simulation.forecast_soil import IFS_DEPTHS

DRAINAGE_SOURCE = "https://www.fao.org/fileadmin/user_upload/faowater/docs/AquaCropV40Chapter3.pdf"


class WaterloggingEstimate(BaseModel):
    method: Literal["root_zone_free_drainage_screening"] = (
        "root_zone_free_drainage_screening"
    )
    accuracy: Literal["not_validated_for_field"] = "not_validated_for_field"
    crop_stress_status: Literal[
        "estimated",
        "crop_parameters_unavailable",
        "aeration_threshold_not_above_field_capacity",
    ]
    days_above_field_capacity: int
    crop_aeration_threshold_vwc: float | None
    days_above_crop_aeration_threshold: int | None
    max_consecutive_days_above_crop_aeration_threshold: int | None
    estimated_crop_stress_days: int | None
    minimum_crop_aeration_factor: float | None
    crop_lag_days: int | None
    saturated_water_content_vwc: float
    effective_ksat_mm_day: float
    peak_end_of_day_water_content_vwc: float
    estimated_drainage_mm: float
    surface_excess_mm: float
    soil_method: str
    soil_sources: list[str]
    drainage_source: str = DRAINAGE_SOURCE
    crop_parameters_source: str | None
    crop_response_source: str | None


@lru_cache(maxsize=1)
def _crop_aeration_data() -> dict:
    resource = files(__package__).joinpath("artifacts", "waterlogging-crops.json")
    return loads(resource.read_text(encoding="utf-8"))


def estimate_waterlogging(
    *,
    crop_id: str,
    weather: Sequence[WeatherDay],
    coefficients: Mapping[date, float],
    irrigation: Mapping[date, float],
    moisture: tuple[float, float, float],
    depth_m: float,
    field_capacity: float,
    wilting_point: float,
    depletion_fraction: float,
    soil: SoilWaterloggingProfile,
) -> tuple[WaterloggingEstimate, frozenset[date] | None] | None:
    weights = [
        (min(depth_m, layer.bottom_m) - layer.top_m, layer)
        for layer in soil.layers
        if layer.top_m < depth_m
    ]
    if abs(fsum(length for length, _ in weights) - depth_m) > 1e-6:
        return None
    saturation = fsum(length * layer.saturation for length, layer in weights) / depth_m
    if not wilting_point < field_capacity < saturation <= 1:
        return None
    # Thickness-weighted harmonic mean for vertical flow through stacked layers.
    ksat = (
        depth_m
        / fsum(length / layer.saturated_conductivity_mm_day for length, layer in weights)
        if all(layer.saturated_conductivity_mm_day > 0 for _, layer in weights)
        else 0.0
    )
    tau = min(1.0, 0.0866 * ksat**0.35)
    data = _crop_aeration_data()
    crop = data["profiles"].get(crop_id)
    threshold = (
        saturation - crop["aeration_threshold_below_saturation_vol_pct"] / 100
        if crop
        else None
    )
    if threshold is None:
        crop_status = "crop_parameters_unavailable"
    elif threshold <= field_capacity:
        # Combining independent soil maps can place the crop threshold below
        # the field's ordinary drained water content. Avoid inferred stress there.
        crop_status = "aeration_threshold_not_above_field_capacity"
    else:
        crop_status = "estimated"
    lag_days = crop["lag_days"] if crop else None
    stress_threshold = threshold if crop_status == "estimated" else None
    root_mm = 1000 * depth_m
    storage = root_mm * min(
        saturation,
        fsum(
            max(0, min(depth_m, bottom) - top) * theta
            for (top, bottom), theta in zip(IFS_DEPTHS, moisture, strict=True)
        ) / depth_m,
    )
    wilting_mm = wilting_point * root_mm
    available_at_stress = (1 - depletion_fraction) * (
        field_capacity - wilting_point
    ) * root_mm
    wet_days = crop_wet_days = consecutive = longest = 0
    stress_dates = set()
    drainage = surface_excess = peak_theta = 0.0
    minimum_aeration_factor = 1.0
    for day in weather:
        available_water = max(0.0, storage - wilting_mm)
        dry_factor = min(1.0, available_water / available_at_stress)
        incoming = storage + day.rain_mm + irrigation.get(day.date, 0.0)
        saturated_storage = saturation * root_mm
        surface_excess += max(0.0, incoming - saturated_storage)
        filled = min(saturated_storage, incoming)
        use = day.reference_et0_mm * coefficients[day.date] * dry_factor
        storage = filled - min(use, max(0.0, filled - wilting_mm))
        above_capacity = max(0.0, storage / root_mm - field_capacity)
        drain_theta = tau * (saturation - field_capacity) * (
            expm1(above_capacity) / expm1(saturation - field_capacity)
        )
        daily_drainage = min(above_capacity, drain_theta) * root_mm
        storage -= daily_drainage
        drainage += daily_drainage
        theta = storage / root_mm
        peak_theta = max(peak_theta, theta)
        wet_days += theta > field_capacity + 1e-9
        if (
            stress_threshold is not None
            and lag_days is not None
            and theta > stress_threshold + 1e-9
        ):
            crop_wet_days += 1
            aeration_factor = 1 - min(consecutive / lag_days, 1.0) * (
                (theta - stress_threshold) / (saturation - stress_threshold)
            )
            minimum_aeration_factor = min(minimum_aeration_factor, aeration_factor)
            if aeration_factor < 1.0:
                stress_dates.add(day.date)
            consecutive += 1
            longest = max(longest, consecutive)
        else:
            consecutive = 0

    stress_estimated = stress_threshold is not None
    estimate = WaterloggingEstimate(
        crop_stress_status=crop_status,
        days_above_field_capacity=wet_days,
        crop_aeration_threshold_vwc=(
            round(threshold, 5) if threshold is not None else None
        ),
        days_above_crop_aeration_threshold=crop_wet_days if stress_estimated else None,
        max_consecutive_days_above_crop_aeration_threshold=(
            longest if stress_estimated else None
        ),
        estimated_crop_stress_days=len(stress_dates) if stress_estimated else None,
        minimum_crop_aeration_factor=round(minimum_aeration_factor, 5) if stress_estimated else None,
        crop_lag_days=lag_days,
        saturated_water_content_vwc=round(saturation, 5),
        effective_ksat_mm_day=round(ksat, 4),
        peak_end_of_day_water_content_vwc=round(peak_theta, 5),
        estimated_drainage_mm=round(drainage, 3),
        surface_excess_mm=round(surface_excess, 3),
        soil_method=soil.method,
        soil_sources=list(soil.source_urls),
        crop_parameters_source=data["sources"]["crop_parameters_url"] if crop else None,
        crop_response_source=data["sources"]["equation_url"] if crop else None,
    )
    return estimate, frozenset(stress_dates) if stress_estimated else None
