from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from field_shift_science.fields.schemas import Season
from field_shift_science.simulation.schemas import WaterForecast, WaterResult, WaterSource
from field_shift_science.weather.historical import WeatherUnavailableError
from field_shift_science.weather.scenarios import Scenario

from field_shift.db import SessionDep
from field_shift.modules.simulation.forecast import latest_water_forecast
from field_shift.modules.simulation.historical import HistoricalInputError
from field_shift.modules.simulation.service import run_water_balance

router = APIRouter(prefix="/water", tags=["water"])


@router.get("/forecast", response_model=WaterForecast | None)
def water_forecast(
    session: SessionDep,
    field_id: Annotated[str, Query(min_length=1, max_length=24)],
    geometry_revision: Annotated[int | None, Query(ge=0)] = None,
) -> WaterForecast | None:
    return latest_water_forecast(session, field_id, geometry_revision=geometry_revision)


@router.get("", response_model=WaterResult)
def water(
    session: SessionDep,
    field_id: Annotated[str, Query(min_length=1, max_length=24)],
    season: Season = 2026,
    scenario: Scenario = Scenario.DRY_YEAR,
    source: WaterSource = WaterSource.DEMO,
    irrigation_mm: Annotated[float, Query(ge=0, le=500, allow_inf_nan=False)] = 0,
    start_date: date | None = None,
    end_date: date | None = None,
    initial_reserve_mm: Annotated[float | None, Query(ge=0, le=2000, allow_inf_nan=False)] = None,
    flowering_start: date | None = None,
    flowering_end: date | None = None,
) -> WaterResult:
    if source == WaterSource.DEMO and any(
        value is not None
        for value in (start_date, end_date, initial_reserve_mm, flowering_start, flowering_end)
    ):
        raise HTTPException(422, "Date and initialization overrides require historical data.")
    try:
        return run_water_balance(
            session,
            field_id=field_id,
            season=season,
            scenario=scenario,
            irrigation_mm=irrigation_mm,
            source=source,
            start_date=start_date,
            end_date=end_date,
            initial_reserve_mm=initial_reserve_mm,
            flowering_start=flowering_start,
            flowering_end=flowering_end,
        )
    except HistoricalInputError as error:
        raise HTTPException(422, str(error)) from error
    except WeatherUnavailableError as error:
        raise HTTPException(503, str(error)) from error
