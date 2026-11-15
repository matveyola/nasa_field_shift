from dataclasses import dataclass
from datetime import date, timedelta
from os import getenv
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, FiniteFloat, ValidationError

OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_CUSTOMER_ARCHIVE = "https://customer-archive-api.open-meteo.com/v1/archive"
OPEN_METEO_DOCUMENTATION = "https://open-meteo.com/en/docs/historical-weather-api"
ERA5_DATASET = "https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels"
ERA5_SPATIAL_RESOLUTION_DEGREES = 0.25
_RAIN = "rain_sum"
_ET0 = "et0_fao_evapotranspiration"
_SOIL = (
    "soil_moisture_0_to_7cm_mean",
    "soil_moisture_7_to_28cm_mean",
    "soil_moisture_28_to_100cm_mean",
)
_MAX_RESPONSE_BYTES = 512 * 1024


class WeatherUnavailableError(RuntimeError):
    """The weather archive did not supply the requested data."""


@dataclass(frozen=True, slots=True)
class ReanalysisDay:
    date: date
    rain_mm: float
    reference_et0_mm: float


@dataclass(frozen=True, slots=True)
class ReanalysisSeries:
    days: tuple[ReanalysisDay, ...]
    grid_latitude: float
    grid_longitude: float
    timezone: str
    prior_day_moisture: tuple[float, float, float] | None


class HistoricalWeatherProvider(Protocol):
    def load(
        self, latitude: float, longitude: float, start: date, end: date
    ) -> ReanalysisSeries: ...


class _ArchiveDaily(BaseModel):
    time: list[date]
    rain_sum: list[FiniteFloat | None]
    et0_fao_evapotranspiration: list[FiniteFloat | None]
    soil_moisture_0_to_7cm_mean: list[FiniteFloat | None] | None = None
    soil_moisture_7_to_28cm_mean: list[FiniteFloat | None] | None = None
    soil_moisture_28_to_100cm_mean: list[FiniteFloat | None] | None = None


class _ArchiveResponse(BaseModel):
    latitude: FiniteFloat
    longitude: FiniteFloat
    timezone: str
    daily_units: dict[str, str]
    daily: _ArchiveDaily


def _soil_profile(response: _ArchiveResponse) -> tuple[float, float, float] | None:
    shallow = response.daily.soil_moisture_0_to_7cm_mean
    middle = response.daily.soil_moisture_7_to_28cm_mean
    deep = response.daily.soil_moisture_28_to_100cm_mean
    if any(response.daily_units.get(column) != "m³/m³" for column in _SOIL):
        return None
    if not shallow or not middle or not deep:
        return None
    first, second, third = shallow[0], middle[0], deep[0]
    if first is None or second is None or third is None:
        return None
    return (
        (first, second, third) if all(0 <= value <= 1 for value in (first, second, third)) else None
    )


class OpenMeteoHistoricalWeather:
    """Fetch a fixed ERA5 dataset with locally aggregated calendar days."""

    def load(self, latitude: float, longitude: float, start: date, end: date) -> ReanalysisSeries:
        previous = start - timedelta(days=1)
        params = {
            "latitude": f"{latitude:.6f}",
            "longitude": f"{longitude:.6f}",
            "start_date": previous.isoformat(),
            "end_date": end.isoformat(),
            "daily": ",".join((_RAIN, _ET0, *_SOIL)),
            "models": "era5",
            "timezone": "auto",
        }
        key = getenv("OPEN_METEO_API_KEY")
        endpoint = OPEN_METEO_ARCHIVE
        if key:
            endpoint = OPEN_METEO_CUSTOMER_ARCHIVE
            params["apikey"] = key
        request = Request(
            f"{endpoint}?{urlencode(params)}",
            headers={"Accept": "application/json", "User-Agent": "FieldShift/0.1"},
        )
        try:
            with urlopen(request, timeout=12) as stream:
                body = stream.read(_MAX_RESPONSE_BYTES + 1)
            if len(body) > _MAX_RESPONSE_BYTES:
                raise WeatherUnavailableError("Open-Meteo returned an oversized response.")
            response = _ArchiveResponse.model_validate_json(body)
        except (OSError, ValidationError):
            # urllib error context can include a credential in the request URL.
            raise WeatherUnavailableError("Open-Meteo historical weather is unavailable.") from None

        daily = response.daily
        expected = [previous + timedelta(days=index) for index in range((end - previous).days + 1)]
        if (
            daily.time != expected
            or len(daily.rain_sum) != len(expected)
            or len(daily.et0_fao_evapotranspiration) != len(expected)
            or response.daily_units.get(_RAIN) != "mm"
            or response.daily_units.get(_ET0) != "mm"
        ):
            raise WeatherUnavailableError(
                "Open-Meteo returned an incomplete historical weather series."
            )

        days: list[ReanalysisDay] = []
        for day, rain, et0 in zip(
            daily.time[1:], daily.rain_sum[1:], daily.et0_fao_evapotranspiration[1:], strict=True
        ):
            if rain is None or et0 is None or rain < 0 or et0 < 0:
                raise WeatherUnavailableError(
                    f"ERA5 rainfall or ET0 is missing for {day.isoformat()}."
                )
            days.append(ReanalysisDay(day, rain, et0))
        return ReanalysisSeries(
            tuple(days),
            response.latitude,
            response.longitude,
            response.timezone,
            _soil_profile(response),
        )
