"""Reference statistics and rainfall erosivity for the global planner."""

from functools import lru_cache
from gzip import decompress
from importlib.resources import files
from json import loads
from math import floor, isfinite
from struct import unpack_from
from typing import Literal

from field_shift.modules.rotations.farmer_priorities import PlanEconomics
from field_shift.modules.rotations.soil_indicators import HarvestRemovalInput

EROSIVITY_SOURCE_URL = "https://zenodo.org/records/11078865"
EROSIVITY_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
Artifact = Literal["economics", "nutrients"]


@lru_cache(maxsize=2)
def global_artifact(name: Artifact) -> dict:
    resource = files(__package__).joinpath("artifacts").joinpath(f"global-{name}.json")
    return loads(resource.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _erosivity_grid() -> bytes:
    resource = (
        files(__package__).joinpath("artifacts").joinpath("global-raster-r.bin.gz")
    )
    data = decompress(resource.read_bytes())
    if len(data) != 1800 * 3600 * 4:
        raise ValueError("Incorrect dimensions for the bundled GloRESatE grid.")
    return data


def rainfall_erosivity(latitude: float, longitude: float) -> float | None:
    column = min(3599, floor((longitude + 180) * 10))
    row = min(1799, floor((90 - latitude) * 10))
    value = unpack_from("<f", _erosivity_grid(), (row * 3600 + column) * 4)[0]
    return round(value, 3) if isfinite(value) and value >= 0 else None


def country_benchmark(country_code: str | None, crop_id: str) -> dict | None:
    country = global_artifact("economics")["countries"].get(country_code)
    return country["crops"].get(crop_id) if country else None


def global_economics(row: dict) -> PlanEconomics:
    return PlanEconomics(
        marketable_yield_t_ha=row.get("marketable_yield_t_ha"),
        farmgate_price_per_t=row.get("farmgate_price_per_t"),
        currency="USD",
        source=row["source"],
        yield_moisture_pct=row.get("yield_moisture_pct"),
    )


def global_harvest(crop_id: str, outcome: PlanEconomics) -> HarvestRemovalInput | None:
    row = global_artifact("nutrients")["crops"].get(crop_id)
    if row is None or outcome.marketable_yield_t_ha is None:
        return None
    return HarvestRemovalInput(
        yield_t_ha=outcome.marketable_yield_t_ha,
        p2o5_kg_per_t=row["p2o5_kg_per_t"],
        k2o_kg_per_t=row["k2o_kg_per_t"],
        source=row["source_url"],
    )
