"""Published planning benchmarks for the packaged Marsden demo point."""

from functools import lru_cache
from importlib.resources import files
from json import loads
from typing import Literal

from pydantic import BaseModel

from field_shift_rotation_model.modules.rotations.farmer_priorities import PlanEconomics
from field_shift_rotation_model.modules.rotations.historical_fields import FieldConfig
from field_shift_rotation_model.modules.rotations.soil_indicators import (
    HarvestRemovalInput,
    RusleInputs,
)

MARSDEN_ID = "marsden_demo"
MARSDEN_COORDINATES = (42.01379, -93.78421)
Artifact = Literal["economics", "nutrients", "erosion"]


class CropBenchmarkUse(BaseModel):
    crop_id: str
    economics_source: Literal["supplied", "published_benchmark", "unavailable"]
    nutrient_source: str | None = None
    nutrient_yield_t_ha: float | None = None
    nutrient_yield_moisture_pct: float | None = None
    p2o5_kg_per_t: float | None = None
    k2o_kg_per_t: float | None = None
    p2o5_removed_kg_ha: float | None = None
    k2o_removed_kg_ha: float | None = None


def applies_to(field: FieldConfig) -> bool:
    return field.id == MARSDEN_ID and (field.latitude, field.longitude) == MARSDEN_COORDINATES


@lru_cache(maxsize=3)
def marsden_artifact(name: Artifact) -> dict:
    resource = files(__package__).joinpath("artifacts").joinpath(f"marsden-{name}.json")
    return loads(resource.read_text(encoding="utf-8"))


def benchmark_economics(crop_id: str, *, uses_irrigation: bool) -> PlanEconomics | None:
    row = marsden_artifact("economics")["crops"].get(crop_id)
    if row is None:
        return None
    return PlanEconomics(
        marketable_yield_t_ha=row.get("marketable_yield_t_ha"),
        farmgate_price_per_t=row.get("farmgate_price_per_t"),
        included_cost_per_ha=None if uses_irrigation else row.get("included_cost_per_ha"),
        currency=row.get("currency"),
        source=row["source"],
        yield_moisture_pct=row.get("yield_moisture_pct"),
    )


def benchmark_harvest(crop_id: str, outcome: PlanEconomics) -> HarvestRemovalInput | None:
    """Apply regional coefficients to yield on the same harvested-weight basis."""
    row = marsden_artifact("nutrients")["crops"].get(crop_id)
    if row is None or outcome.marketable_yield_t_ha is None:
        return None
    basis = row["yield_moisture_pct"]
    yield_basis = outcome.yield_moisture_pct
    if yield_basis is None:
        return None
    equivalent_yield = outcome.marketable_yield_t_ha * (100 - yield_basis) / (100 - basis)
    return HarvestRemovalInput(
        yield_t_ha=equivalent_yield,
        p2o5_kg_per_t=row["p2o5_kg_per_t"],
        k2o_kg_per_t=row["k2o_kg_per_t"],
        source=row["source_url"],
    )


def benchmark_erosion(shared: dict, *, c: float, p: float, source: str) -> RusleInputs | None:
    if not all(shared.get(factor) is not None for factor in ("r", "k", "ls")):
        return None
    return RusleInputs(
        r=shared["r"],
        k=shared["k"],
        ls=shared["ls"],
        c=c,
        p=p,
        source=f"{shared['source']}; C/P: {source}",
    )
