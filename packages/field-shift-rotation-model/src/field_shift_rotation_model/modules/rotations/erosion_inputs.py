"""Optional inputs for point screening with the Revised Universal Soil Loss Equation."""

import zlib
from dataclasses import dataclass
from functools import lru_cache
from http.client import HTTPException
from math import exp, hypot, isfinite
from struct import error as StructError

from field_shift_rotation_model.modules.rotations.global_soil import (
    SOILGRIDS_SOURCE_URL,
    WCS_POOL,
    coverage_url,
    sample_soilgrids_pixel,
    validate_soil_coordinates,
)

EPIC_SOURCE_URL = (
    "https://storage.googleapis.com/releases.naturalcapitalproject.org/"
    "invest-userguide/latest/en/sdr.html#soil-erodibility-k"
)
LS_SOURCE_URL = (
    "https://www.ars.usda.gov/ARSUserFiles/60600505/RUSLE/AH_703%20-"
    "%20Predicting%20Soil%20Erosion%20by%20Water%20-%20A%20Guide%20to"
    "%20Conservation%20Planning%20With%20the%20RUSLE%20%28RUSLE%29.pdf"
)
TOPSOIL_PROPERTIES = ("sand", "silt", "clay", "soc")
TOPSOIL_DEPTH_CM = (0, 5)
MIN_UNIFORM_SLOPE_LENGTH_M = 15 * 0.3048


class ErodibilityProviderError(RuntimeError):
    """The soil map could not supply a usable point estimate."""


@dataclass(frozen=True, slots=True)
class Erodibility:
    k: float
    sand_pct: float
    silt_pct: float
    clay_pct: float
    organic_carbon_pct: float
    source: str = (
        f"EPIC soil erodibility ({EPIC_SOURCE_URL}) from predicted 0–5 cm "
        f"ISRIC SoilGrids mean texture and organic carbon ({SOILGRIDS_SOURCE_URL}). "
        "250 m point screening estimate."
    )


def epic_k(sand_pct: float, silt_pct: float, clay_pct: float, organic_carbon_pct: float) -> float:
    """Estimate K in metric t ha h/(ha MJ mm) from mass percentages."""
    texture = (sand_pct, silt_pct, clay_pct)
    if (
        not all(isfinite(value) and 0 <= value <= 100 for value in texture)
        or abs(sum(texture) - 100) > 0.5
        or not isfinite(organic_carbon_pct)
        or not 0 <= organic_carbon_pct <= 100
    ):
        raise ValueError("Expected valid topsoil mass percentages for EPIC erodibility.")
    fine = silt_pct + clay_pct
    if fine == 0:
        raise ValueError("EPIC erodibility is undefined without silt or clay.")

    # SoilGrids stores texture as rounded integers whose total may differ slightly.
    texture_total = sum(texture)
    sand_fraction = sand_pct / texture_total
    silt_fraction = silt_pct / texture_total
    non_sand = 1 - sand_fraction
    coarse_sand_effect = 0.2 + 0.3 * exp(-2.56 * sand_fraction * (1 - silt_fraction))
    clay_silt_effect = (silt_pct / fine) ** 0.3
    carbon = organic_carbon_pct
    carbon_effect = 1 - 0.25 * carbon / (carbon + exp(3.72 - 2.95 * carbon))
    high_sand_effect = 1 - 0.7 * non_sand / (non_sand + exp(-5.51 + 22.9 * non_sand))
    # Convert from U.S. customary K to metric K before multiplying by metric R.
    return 0.1317 * coarse_sand_effect * clay_silt_effect * carbon_effect * high_sand_effect


@lru_cache(maxsize=128)
def fetch_erodibility(latitude: float, longitude: float) -> Erodibility:
    validate_soil_coordinates(latitude, longitude)
    samples = [
        WCS_POOL.submit(
            sample_soilgrids_pixel,
            coverage_url(property_id, TOPSOIL_DEPTH_CM, latitude, longitude),
        )
        for property_id in TOPSOIL_PROPERTIES
    ]
    try:
        sand, silt, clay, carbon = (sample.result() for sample in samples)
        # 1% = 10 g/kg = 100 dg/kg.
        texture_pct = (sand / 10, silt / 10, clay / 10)
        carbon_pct = carbon / 100
        return Erodibility(epic_k(*texture_pct, carbon_pct), *texture_pct, carbon_pct)
    except (OSError, HTTPException, StructError, ValueError, zlib.error) as error:
        for sample in samples:
            sample.cancel()
        raise ErodibilityProviderError(
            "ISRIC SoilGrids could not supply topsoil texture and organic carbon "
            "at these coordinates. Supply a local metric K factor or retry."
        ) from error


def uniform_slope_ls(slope_percent: float, slope_length_m: float) -> float:
    """AH703 eq. 4-1 to 4-5 for a uniform, moderately erodible cropland slope."""
    if (
        not isfinite(slope_percent)
        or slope_percent < 0
        or not isfinite(slope_length_m)
        or slope_length_m < MIN_UNIFORM_SLOPE_LENGTH_M
    ):
        raise ValueError(
            "A uniform slope needs nonnegative grade (%) and at least 4.572 m "
            "of horizontal overland flow length. Supply LS directly otherwise."
        )
    grade = slope_percent / 100
    sine = grade / hypot(1, grade)
    relative_rill = (sine / 0.0896) / (3 * sine**0.8 + 0.56)
    exponent = relative_rill / (1 + relative_rill)
    length_factor = (slope_length_m / (72.6 * 0.3048)) ** exponent
    steepness_factor = 10.8 * sine + 0.03 if slope_percent < 9 else 16.8 * sine - 0.50
    return length_factor * steepness_factor
