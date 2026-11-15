"""Financial benchmarks for the climate planner; amounts are per hectare."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from field_shift.modules.rotations.farmer_priorities import Currency, Nonnegative
from field_shift.modules.rotations.global_defaults import (
    country_benchmark,
    global_economics,
)
from field_shift.modules.rotations.marsden_defaults import (
    benchmark_economics,
    marsden_artifact,
)

IOWA_SEED_BUDGET = "https://www.extension.iastate.edu/agdm/crops/pdf/a1-20.pdf"
NDSU_SEED_BUDGET = "https://www.ndsu.edu/agriculture/sites/default/files/2026-02/ec1659.pdf"
ACRE_IN_HECTARES = 0.40468564224
IOWA_SEED_REFERENCES = {
    "maize_grain": (113.70, "Iowa", IOWA_SEED_BUDGET, 3, "30,000 kernels per acre"),
    "soybeans": (72.11, "Iowa", IOWA_SEED_BUDGET, 5, "160,000 seeds per acre"),
    "sunflower": (37.40, "Southeast North Dakota", NDSU_SEED_BUDGET, 6, "Oilseed sunflower"),
    "wheat_spring": (22.75, "Southeast North Dakota", NDSU_SEED_BUDGET, 3, "Spring wheat"),
}


class SeedCostInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cost_per_ha: Nonnegative
    currency: Currency
    source: Annotated[str, Field(min_length=1, max_length=400)]


class FinanceInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marketable_yield_t_ha: Nonnegative
    farmgate_price_per_t: Nonnegative
    currency: Currency
    source: Annotated[str, Field(min_length=1, max_length=400)]


def season_economics(
    crop_id, supplied: FinanceInputs | None, *, country_code, marsden,
    region_code=None, seed_cost: SeedCostInput | None = None,
):
    reference_details = None
    price_years = []
    if supplied is not None:
        yield_t_ha = supplied.marketable_yield_t_ha
        price = supplied.farmgate_price_per_t
        currency = supplied.currency
        source = supplied.source
        origin = "supplied_planning_assumptions"
    else:
        reference = benchmark_economics(crop_id, uses_irrigation=False) if marsden else None
        if reference is not None:
            raw = marsden_artifact("economics")["crops"][crop_id]["raw"]
            reference_details = {key: raw[key] for key in ("yield", "price") if key in raw}
            if raw.get("price"):
                price_years = [raw["price"]["year"]]
        else:
            row = country_benchmark(country_code, crop_id)
            reference = global_economics(row) if row else None
            if row:
                reference_details = {key: value for key, value in row.items() if key != "included_cost_per_ha"}
                price_years = sorted({observation["year"] for observation in row["raw"]["price"]})
        yield_t_ha = reference.marketable_yield_t_ha if reference else None
        price = reference.farmgate_price_per_t if reference else None
        currency = reference.currency if reference else None
        source = reference.source if reference else None
        origin = "published_yield_and_price_reference" if reference else "unavailable"
    revenue = round(yield_t_ha * price, 2) if yield_t_ha is not None and price is not None else None
    seed = None
    if seed_cost is not None:
        seed = seed_cost.model_dump() | {"basis": "supplied"}
    elif region_code == "US-IA" and crop_id in IOWA_SEED_REFERENCES:
        per_acre, geography, url, page, note = IOWA_SEED_REFERENCES[crop_id]
        seed = {
            "cost_per_ha": per_acre / ACRE_IN_HECTARES, "cost_per_acre": per_acre,
            "currency": "USD", "year": 2026, "geography": geography,
            "source": f"{url}#page={page}", "note": note,
            "basis": "iowa_budget" if geography == "Iowa" else "cross_region_budget_proxy",
        }
    if seed:
        seed["cost_per_ha"] = round(seed["cost_per_ha"], 2)
    after_seed = revenue - seed["cost_per_ha"] if revenue is not None and seed and seed["currency"] == currency else None
    return {
        "marketable_yield_t_ha": yield_t_ha,
        "farmgate_price_per_t": price,
        "gross_revenue_per_ha": revenue,
        "seed_cost": seed,
        "revenue_after_seed_per_ha": round(after_seed, 2) if after_seed is not None else None,
        "price_reference_years": price_years,
        "currency": currency,
        "origin": origin,
        "source": source,
        "benchmark": reference_details,
        "method": "Marketable yield × farm-gate price; revenue after seed subtracts only planting seed cost.",
        "field_validation_error": None,
    }
