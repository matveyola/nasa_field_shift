from datetime import date

from field_shift_science.simulation.schemas import WaterResult, WaterSource
from field_shift_science.simulation.water_balance import DemoWaterModel, WaterInputs, WaterModel
from field_shift_science.weather.historical import (
    HistoricalWeatherProvider,
    OpenMeteoHistoricalWeather,
)
from field_shift_science.weather.scenarios import Scenario
from sqlalchemy.orm import Session

from field_shift.modules.fields.history import boundary_for_season
from field_shift.modules.fields.service import require_field
from field_shift.modules.simulation.historical import HistoricalWaterModel

DEFAULT_WATER_MODEL: WaterModel = DemoWaterModel()


def calculate_water_balance(
    inputs: WaterInputs,
    model: WaterModel | None = None,
    *,
    weather_provider: HistoricalWeatherProvider | None = None,
) -> WaterResult:
    if model is not None:
        return model.run(inputs)
    if inputs.source == WaterSource.HISTORICAL:
        weather = weather_provider if weather_provider is not None else OpenMeteoHistoricalWeather()
        return HistoricalWaterModel(weather).run(inputs)
    return DEFAULT_WATER_MODEL.run(inputs)


def run_water_balance(
    session: Session,
    *,
    field_id: str,
    season: int,
    scenario: Scenario | str,
    irrigation_mm: float = 0,
    model: WaterModel | None = None,
    source: WaterSource = WaterSource.DEMO,
    start_date: date | None = None,
    end_date: date | None = None,
    initial_reserve_mm: float | None = None,
    flowering_start: date | None = None,
    flowering_end: date | None = None,
) -> WaterResult:
    field = require_field(session, field_id)
    geometry, _, _ = boundary_for_season(session, field, season)
    inputs = WaterInputs(
        field.id,
        field.crop,
        season,
        Scenario(scenario),
        irrigation_mm,
        source=source,
        geometry=geometry,
        start_date=start_date,
        end_date=end_date,
        initial_reserve_mm=initial_reserve_mm,
        flowering_start=flowering_start,
        flowering_end=flowering_end,
    )
    if source == WaterSource.HISTORICAL:
        # The archive call must not hold a SQL connection while waiting on the network.
        session.rollback()
    return calculate_water_balance(inputs, model)
