"""Botanical groups and sources, not inferred crop suitability or yield coefficients."""

CROP_FAMILIES = {
    "Wheat": "Poaceae",
    "Maize": "Poaceae",
    "Soy": "Fabaceae",
    "Barley": "Poaceae",
    "Sunflower": "Asteraceae",
    "Rapeseed": "Brassicaceae",
    "Peas": "Fabaceae",
}

SOURCES = (
    "https://www.nrcs.usda.gov/resources/guides-and-instructions/"
    "conservation-crop-rotation-ac-328-conservation-practice-standard",
    "https://extension.umn.edu/agriculture/crop-production/small-grains/small-grain-crop-rotations",
)


def known_crop(name: str) -> str | None:
    aliases = {
        "corn": "Maize",
        "soybean": "Soy",
        "soybeans": "Soy",
        "spring wheat": "Wheat",
        "winter wheat": "Wheat",
        "canola": "Rapeseed",
        "pea": "Peas",
    }
    normalized = name.strip().casefold()
    return aliases.get(normalized) or next(
        (crop for crop in CROP_FAMILIES if crop.casefold() == normalized), None
    )
