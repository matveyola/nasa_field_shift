"""Estimate hydraulic inputs from SoilGrids using published pedotransfer equations."""

import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from http.client import HTTPException
from struct import error as StructError

from field_shift.modules.rotations.global_soil import (
    DEPTHS_CM,
    SOILGRIDS_SOURCE_URL,
    WCS_POOL,
    SoilProviderError,
    coverage_url,
    sample_soilgrids_pixel,
    validate_soil_coordinates,
)

# Raw SoilGrids units in this order: cg/cm³, g/kg, g/kg, pH × 10, mmol(c)/kg.
HYDRAULIC_PROPERTIES = ("bdod", "clay", "silt", "phh2o", "cec")
SOILGRIDS_WCS_URL = "https://docs.isric.org/globaldata/soilgrids/wcs.html"
HIHYDROSOIL_METHOD_URL = (
    "https://www.futurewater.nl/wp-content/uploads/2020/10/"
    "HiHydroSoil-v2.0-High-Resolution-Soil-Maps-of-Global-Hydraulic-Properties_v2.pdf"
)
# Frozen SoilGrids mean point samples. Rows follow DEPTHS_CM, columns follow
# HYDRAULIC_PROPERTIES. Use these only at the exact coordinates queried.
BUNDLED_WATERLOGGING_SAMPLES = {
    (42.01379, -93.78421): (  # Marsden Farm, Iowa
        (145, 335, 456, 66, 303),
        (158, 337, 457, 65, 305),
        (158, 304, 478, 66, 297),
        (161, 310, 472, 67, 286),
        (171, 306, 450, 73, 235),
    ),
    (49.15, 28.47): (  # Vinnytsia region, Ukraine
        (123, 267, 486, 65, 261),
        (130, 284, 479, 66, 224),
        (135, 294, 457, 66, 197),
        (141, 316, 448, 66, 192),
        (144, 327, 442, 69, 179),
    ),
    (-24.91, -53.40): (  # Paraná, Brazil
        (109, 562, 284, 53, 190),
        (108, 568, 284, 53, 201),
        (114, 598, 261, 53, 171),
        (111, 635, 234, 53, 159),
        (114, 639, 231, 53, 149),
    ),
}


@dataclass(frozen=True, slots=True)
class WaterloggingLayer:
    top_m: float
    bottom_m: float
    saturation: float
    saturated_conductivity_mm_day: float


@dataclass(frozen=True, slots=True)
class SoilWaterloggingProfile:
    layers: tuple[WaterloggingLayer, ...]
    source_urls: tuple[str, ...] = (
        SOILGRIDS_SOURCE_URL,
        SOILGRIDS_WCS_URL,
        HIHYDROSOIL_METHOD_URL,
    )
    method: str = "soilgrids_mean_hihydrosoil_v2_pedotransfer"


def _derive_layer(
    depth_cm: tuple[int, int], samples: Sequence[int]
) -> WaterloggingLayer:
    top_cm, bottom_cm = depth_cm
    bdod, raw_clay, raw_silt, phh2o, cec = samples
    bulk_density_g_cm3 = bdod / 100
    clay_percent = raw_clay / 10
    silt_percent = raw_silt / 10
    ph_in_water = phh2o / 10
    cec_meq_100g = cec / 10  # 1 cmol(c)/kg equals 1 meq/100 g.
    if not (
        0 < bulk_density_g_cm3 < 3
        and clay_percent + silt_percent <= 100
        and 0 < ph_in_water <= 14
    ):
        raise ValueError("Invalid SoilGrids inputs to the pedotransfer equations")

    # HiHydroSoil v2 technical report, p. 8. The regression uses 0–30 cm
    # as topsoil and estimates conductivity in cm/day.
    saturation = (
        0.83080
        - 0.28217 * bulk_density_g_cm3
        + 0.0002728 * clay_percent
        + 0.000187 * silt_percent
    )
    log10_conductivity_cm_day = (
        0.40220
        + 0.26122 * ph_in_water
        + 0.44565 * (bottom_cm <= 30)
        - 0.02329 * clay_percent
        - 0.01265 * silt_percent
        - 0.01038 * cec_meq_100g
    )
    if not 0 < saturation <= 1:
        raise ValueError("Pedotransfer equation returned an invalid saturation")

    return WaterloggingLayer(
        top_m=top_cm / 100,
        bottom_m=bottom_cm / 100,
        saturation=saturation,
        saturated_conductivity_mm_day=10 * 10**log10_conductivity_cm_day,
    )


@lru_cache(maxsize=128)
def fetch_waterlogging_inputs(
    latitude: float, longitude: float
) -> SoilWaterloggingProfile:
    """Fetch the five 0–100 cm layers and derive saturation and conductivity.

    The regressions use European soil samples and the inputs are SoilGrids
    model means. Field drainage also depends on terrain, water table and
    installed drains. Three exact coordinates use bundled point samples.
    Missing or invalid mapped values elsewhere raise SoilProviderError.
    """
    validate_soil_coordinates(latitude, longitude)
    bundled_samples = BUNDLED_WATERLOGGING_SAMPLES.get((latitude, longitude))
    if bundled_samples is not None:
        return SoilWaterloggingProfile(
            layers=tuple(
                _derive_layer(depth, samples)
                for depth, samples in zip(DEPTHS_CM, bundled_samples, strict=True)
            ),
            method="bundled_soilgrids_mean_hihydrosoil_v2_pedotransfer",
        )

    layer_samples = [
        [
            WCS_POOL.submit(
                sample_soilgrids_pixel, coverage_url(prop, depth, latitude, longitude)
            )
            for prop in HYDRAULIC_PROPERTIES
        ]
        for depth in DEPTHS_CM
    ]
    try:
        layers = tuple(
            _derive_layer(depth, [sample.result() for sample in samples])
            for depth, samples in zip(DEPTHS_CM, layer_samples, strict=True)
        )
    except (OSError, HTTPException, StructError, ValueError, zlib.error) as error:
        for samples in layer_samples:
            for sample in samples:
                sample.cancel()
        raise SoilProviderError(
            "ISRIC SoilGrids could not supply the inputs for all five saturation "
            "and conductivity layers at these coordinates. Use measured hydraulic "
            "layers or retry."
        ) from error
    return SoilWaterloggingProfile(layers=layers)
