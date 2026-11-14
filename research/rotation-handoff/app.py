from datetime import UTC, datetime
from logging import getLogger
from os import environ

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from field_shift.modules.rotations import (
    assessment,
    climate_balance,
    climate_planner,
    climate_weather,
    hybrid_forecast,
    supplied_balance,
)
from field_shift.modules.rotations.climate_reference import ClimateReferenceError
from field_shift.modules.rotations.equipment import EQUIPMENT, EquipmentOption
from field_shift.modules.rotations.farmer_priorities import Priority
from field_shift.modules.rotations.global_defaults import (
    EROSIVITY_LICENSE_URL,
    EROSIVITY_SOURCE_URL,
    global_artifact,
)
from field_shift.modules.rotations.global_soil import SoilProviderError
from field_shift.modules.rotations.historical_fields import load_fields
from field_shift.modules.rotations.hybrid_profiles import ForecastCrop, crop_catalog
from field_shift.modules.rotations.hybrid_weather import WeatherProviderError
from field_shift.modules.rotations.marsden_defaults import MARSDEN_ID, marsden_artifact
from field_shift.modules.rotations.rotation_agronomy import (
    HybridReference,
    cultivar_references,
)
from replay_sutherland_2026_28 import Sutherland2027Result, run_sutherland_2027

logger = getLogger(__name__)

app = FastAPI(
    title="Field Shift: Rotation Assessment and Forecast",
    version="2.13.0",
    description="Compare crop plans using historical weather, short-term forecasts or conditional NASA climate scenarios for the 2040s.",
    servers=[{"url": "http://127.0.0.1:8081", "description": "Local launch"}],
)


class ForecastCatalog(BaseModel):
    fields: list[assessment.FieldSummary]
    crops: list[ForecastCrop]
    equipment: tuple[EquipmentOption, ...]
    priorities: tuple[Priority, ...]
    countries_with_economics: list[str]


@app.get("/api/rotations/assessment/catalog", response_model=assessment.Catalog)
def get_catalog() -> assessment.Catalog:
    return assessment.assessment_catalog()


@app.get("/api/rotations/cultivar-references", response_model=list[HybridReference])
def get_cultivar_references(
    crop_id: str | None = None, region_code: str | None = None,
) -> list[HybridReference]:
    """List trial references, with optional exact crop and region filters."""
    region = region_code.strip().upper() if region_code is not None else None
    return [
        row for row in cultivar_references()
        if (crop_id is None or crop_id == row.crop_id)
        and (region is None or region == row.region_code)
    ]


@app.post("/api/rotations/assessment", response_model=assessment.AssessmentResult)
def compare_rotations(
    request: assessment.AssessmentRequest,
) -> assessment.AssessmentResult:
    field = load_fields().get(request.field_id)
    if field is None:
        raise HTTPException(
            status_code=404, detail="Field not found. Check the assessment catalog."
        )
    return assessment.assess(request, field)


@app.get("/api/rotations/forecast/catalog", response_model=ForecastCatalog)
def get_forecast_catalog() -> ForecastCatalog:
    return ForecastCatalog(
        fields=assessment.assessment_catalog().fields,
        crops=crop_catalog(),
        equipment=EQUIPMENT,
        priorities=(
            "water_stress",
            "water_deficit",
            "water_saving",
            "yield",
            "revenue",
            "soil_cover",
        ),
        countries_with_economics=sorted(global_artifact("economics")["countries"]),
    )


@app.get("/api/rotations/forecast/references/{field_id}")
def get_forecast_references(field_id: str) -> dict:
    if field_id != MARSDEN_ID:
        raise HTTPException(
            status_code=404, detail="No bundled reference data for this field."
        )
    return {
        name: marsden_artifact(name) for name in ("economics", "nutrients", "erosion")
    }


@app.get("/api/rotations/forecast/countries/{country_code}")
def get_country_references(country_code: str) -> dict:
    economics = global_artifact("economics")
    if not (
        len(country_code) == 3 and country_code.isascii() and country_code.isalpha()
    ):
        raise HTTPException(status_code=422, detail="Use an ISO alpha-3 country code.")
    code = country_code.upper()
    return {
        "country_code": code,
        "economics": economics["countries"].get(code),
        "economics_source_urls": economics["source_urls"],
        "nutrients": global_artifact("nutrients"),
        "erosivity_source_url": EROSIVITY_SOURCE_URL,
        "erosivity_license_url": EROSIVITY_LICENSE_URL,
    }


@app.post(
    "/api/rotations/compare-water-balances",
    response_model=supplied_balance.SuppliedBalanceResult,
)
def compare_water_balances(
    request: supplied_balance.CompareSuppliedBalance,
) -> supplied_balance.SuppliedBalanceResult:
    return supplied_balance.compare(request)


@app.get("/api/rotations/climate/catalog")
def get_climate_catalog() -> dict:
    return {
        "fields": assessment.assessment_catalog().fields,
        "crops": [crop for crop in crop_catalog() if crop.id in climate_balance.SUPPORTED_CROPS],
        "equipment": EQUIPMENT,
        "priorities": climate_planner.CLIMATE_PRIORITIES,
        "models": climate_weather.CLIMATE_MODELS,
        "scenarios": climate_weather.CLIMATE_SCENARIOS,
        "planting_years": list(range(2040, 2050)),
        "planting_months": list(range(3, 8)),
        "max_decisions": 3,
        "model_configured": climate_planner.model_is_configured(),
    }


def _climate_response(calculate, request) -> dict:
    try:
        return calculate(request)
    except climate_planner.UnknownClimateField as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except climate_planner.ClimateModelUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (climate_weather.ClimateWeatherError, ClimateReferenceError, SoilProviderError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/rotations/climate/step", response_model=climate_planner.ClimateStepResult)
def recommend_climate_season(request: climate_planner.ClimateStepRequest) -> dict:
    return _climate_response(climate_planner.step, request)


@app.post("/api/rotations/climate/compare", response_model=climate_planner.ClimatePlansResult)
def compare_climate_rotations(request: climate_planner.ClimatePlansRequest) -> dict:
    return _climate_response(climate_planner.compare, request)


@app.post(
    "/api/rotations/scenarios/sutherland-2027",
    response_model=Sutherland2027Result,
    responses={503: {"description": "Ruslan engine or bundled scenario data unavailable"}},
)
def calculate_sutherland_2027() -> Sutherland2027Result:
    """Recalculate the Sutherland 2027 scenario at fixed historical/NASA weights of 50/50."""
    if not climate_planner.model_is_configured():
        raise HTTPException(
            status_code=503,
            detail="Configure FIELDSHIFT_RUSLAN_DIR to a directory containing fstasks/balance.py.",
        )
    try:
        return run_sutherland_2027(environ[climate_planner.MODEL_ENV])
    except (OSError, EOFError, ValueError, KeyError, ClimateReferenceError) as error:
        logger.exception("Sutherland 2027 scenario could not run")
        raise HTTPException(
            status_code=503,
            detail="The Sutherland 2027 scenario could not run. Check the engine and bundled inputs.",
        ) from error


@app.post("/api/rotations/forecast", response_model=hybrid_forecast.ForecastResult)
def forecast_rotations(
    request: hybrid_forecast.ForecastRequest,
) -> hybrid_forecast.ForecastResult:
    field = request.field
    try:
        as_of = datetime.now(UTC).date()
        if field is None:
            resolved = load_fields().get(request.field_id)
            if resolved is None:
                raise HTTPException(status_code=404, detail="Unknown field_id.")
            return hybrid_forecast.forecast(request, resolved, as_of=as_of)
        return hybrid_forecast.forecast(request, field, as_of=as_of)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (WeatherProviderError, SoilProviderError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
