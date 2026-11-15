from dataclasses import dataclass

FAO56_CROP_COEFFICIENTS = "https://www.fao.org/4/x0490e/x0490e0b.htm"
FAO56_WATER_STRESS = "https://www.fao.org/4/x0490e/x0490e0e.htm"


@dataclass(frozen=True, slots=True)
class CropWaterParameters:
    midseason_coefficient: float
    root_depth_m: float
    depletion_fraction: float


# FAO-56 Tables 12 and 22. Use the lower end of the listed rooting depth range.
_MAIZE = CropWaterParameters(1.20, 1.0, 0.55)
_SOY = CropWaterParameters(1.15, 0.6, 0.50)
_SPRING_WHEAT = CropWaterParameters(1.15, 1.0, 0.55)
# Oilseed Kc ranges are 1.0–1.15; retain the upper tabulated value for this
# fixed-midseason reference, not an assertion about local rainfed density.
_SUNFLOWER = CropWaterParameters(1.15, 0.8, 0.45)
_RAPESEED = CropWaterParameters(1.15, 1.0, 0.60)
_BARLEY = CropWaterParameters(1.15, 1.0, 0.55)
# The allocation catalog's Peas means dry/seed peas, not fresh peas (p=0.35).
_DRY_PEAS = CropWaterParameters(1.15, 0.6, 0.40)
_WATER_CATALOG = {
    "maize": _MAIZE,
    "corn": _MAIZE,
    "soy": _SOY,
    "soybean": _SOY,
    "soybeans": _SOY,
    "wheat": _SPRING_WHEAT,
    "spring wheat": _SPRING_WHEAT,
    "barley": _BARLEY,
    "sunflower": _SUNFLOWER,
    "rapeseed": _RAPESEED,
    "canola": _RAPESEED,
    "peas": _DRY_PEAS,
    "dry peas": _DRY_PEAS,
}


def crop_water_parameters(crop: str) -> CropWaterParameters | None:
    return _WATER_CATALOG.get(crop.strip().casefold())
