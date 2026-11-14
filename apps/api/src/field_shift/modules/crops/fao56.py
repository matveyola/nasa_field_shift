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
_WATER_CATALOG = {
    "maize": _MAIZE,
    "corn": _MAIZE,
    "soy": _SOY,
    "soybean": _SOY,
    "soybeans": _SOY,
    "wheat": _SPRING_WHEAT,
    "spring wheat": _SPRING_WHEAT,
}


def crop_water_parameters(crop: str) -> CropWaterParameters | None:
    return _WATER_CATALOG.get(crop.strip().casefold())
