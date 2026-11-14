from fastapi import APIRouter, HTTPException

from field_shift.db import SessionDep
from field_shift.modules.fields.history import boundary_for_season
from field_shift.modules.fields.schemas import Season
from field_shift.modules.fields.service import require_field
from field_shift.modules.weather.historical import WeatherUnavailableError
from field_shift.modules.weather.seasonal import (
    SeasonalWeather,
    WeatherForecast,
    load_seasonal_weather,
    load_weather_forecast,
)

router = APIRouter(prefix="/fields", tags=["weather"])


@router.get("/{field_id}/seasonal-weather", response_model=SeasonalWeather | None)
def seasonal_weather(field_id: str, season: Season, session: SessionDep) -> SeasonalWeather | None:
    field = require_field(session, field_id)
    revision = field.boundary_revision
    geometry, _, _ = boundary_for_season(session, field, season)
    # Release the read transaction before contacting the archive.
    session.close()
    try:
        return load_seasonal_weather(field_id, revision, geometry, season)
    except WeatherUnavailableError as error:
        raise HTTPException(503, str(error)) from None


@router.get("/{field_id}/weather-forecast", response_model=WeatherForecast)
def weather_forecast(field_id: str, session: SessionDep) -> WeatherForecast:
    field = require_field(session, field_id)
    revision, geometry = field.boundary_revision, field.geometry
    session.close()
    try:
        return load_weather_forecast(field_id, revision, geometry)
    except WeatherUnavailableError as error:
        raise HTTPException(503, str(error)) from None
