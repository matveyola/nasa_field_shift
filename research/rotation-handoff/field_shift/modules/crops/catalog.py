from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CropProfile:
    water_use_factor: float
    flowering_start_day: int
    flowering_end_day: int


_GENERIC = CropProfile(0.92, 3, 7)
_CATALOG = {
    "maize": CropProfile(1.0, 1, 5),
    "soy": CropProfile(0.965, 8, 10),
    "wheat": CropProfile(0.85, 2, 5),
}


def demo_crop_profile(crop: str) -> CropProfile:
    """These profiles only parameterize the illustrative August fixture."""
    return _CATALOG.get(crop.strip().casefold(), _GENERIC)
