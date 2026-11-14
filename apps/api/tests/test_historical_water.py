import json
from dataclasses import replace
from datetime import date
from io import BytesIO
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest
from field_shift_science.simulation.schemas import WaterSource
from field_shift_science.simulation.water_balance import WaterInputs
from field_shift_science.weather import historical
from field_shift_science.weather.historical import (
    OpenMeteoHistoricalWeather,
    ReanalysisDay,
    ReanalysisSeries,
    WeatherUnavailableError,
)
from field_shift_science.weather.scenarios import Scenario

from field_shift.modules.simulation.historical import HistoricalInputError, historical_window
from field_shift.modules.simulation.service import calculate_water_balance

_START = date(2024, 8, 1)
_END = date(2024, 8, 2)
_INPUTS = WaterInputs(
    "F-03",
    "Maize",
    2024,
    Scenario.BASELINE,
    source=WaterSource.HISTORICAL,
    start_date=_START,
    end_date=_END,
    geometry={
        "type": "Polygon",
        "coordinates": [[[-94, 41], [-93, 41], [-93, 42], [-94, 42], [-94, 41]]],
    },
)


class FixtureWeather:
    def __init__(self, moisture: tuple[float, float, float] | None = (0.30, 0.20, 0.20)) -> None:
        self.moisture = moisture

    def load(self, latitude: float, longitude: float, start: date, end: date) -> ReanalysisSeries:
        assert (latitude, longitude) == (41.5, -93.5)
        assert (start, end) == (_START, _END)
        return ReanalysisSeries(
            days=(ReanalysisDay(_START, 4, 6.75), ReanalysisDay(_END, 120, 5)),
            grid_latitude=41.5,
            grid_longitude=-93.5,
            timezone="America/Chicago",
            prior_day_moisture=self.moisture,
        )


def test_historical_balance_has_coherent_terms_and_provenance() -> None:
    inputs = replace(_INPUTS, scenario=Scenario.DRY_YEAR, irrigation_mm=1, initial_reserve_mm=50)
    result = calculate_water_balance(inputs, weather_provider=FixtureWeather())
    assert not result.demo
    assert result.source == WaterSource.HISTORICAL
    assert result.threshold_mm == 67.5
    assert result.stress_days == 1
    assert result.first_stress_date == _START
    assert result.flowering_start is None
    assert result.flowering_end is None
    first, second = result.days
    assert (first.source_rain_mm, first.rain_mm, first.reference_et0_mm) == (4, 2, 6.75)
    assert (first.irrigation_mm, second.irrigation_mm) == (1, 0)
    assert (first.water_use_mm, first.reserve_mm, second.opening_mm) == (6, 47, 47)
    assert result.min_reserve_mm == first.reserve_mm
    for day in result.days:
        assert day.reserve_mm == pytest.approx(
            day.opening_mm + day.rain_mm + day.irrigation_mm - day.water_use_mm - day.drainage_mm
        )
    assert result.provenance is not None
    assert result.provenance.rainfall_multiplier == 0.5
    assert result.provenance.weather_model == "ECMWF ERA5"
    assert result.provenance.initial_reserve_source == "user_input"
    assert result.provenance.initial_reserve_mm == 50


def test_previous_day_soil_profile_is_weighted_and_storage_capacity_enforced() -> None:
    inputs = replace(_INPUTS, irrigation_mm=150, flowering_start=_START, flowering_end=_END)
    result = calculate_water_balance(inputs, weather_provider=FixtureWeather())
    assert result.provenance is not None
    assert result.provenance.initial_reserve_source == "era5_prior_day"
    assert result.provenance.initial_moisture_date == date(2024, 7, 31)
    assert result.provenance.initial_theta_m3_m3 == 0.207
    assert result.provenance.initial_reserve_mm == 57
    assert result.days[0].reserve_mm == result.provenance.capacity_mm == 150
    assert result.days[0].drainage_mm > 0
    assert result.flowering_start == _START
    assert result.flowering_end == _END


def test_missing_initial_profile_never_uses_demo_data() -> None:
    with pytest.raises(WeatherUnavailableError, match="Prior-day ERA5 soil moisture"):
        calculate_water_balance(_INPUTS, weather_provider=FixtureWeather(moisture=None))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"crop": "Unknown crop"}, "supports maize, soy and wheat"),
        ({"initial_reserve_mm": 151}, "between 0 and 150 mm"),
        ({"flowering_start": _START}, "both flowering"),
        ({"flowering_start": date(2024, 8, 3), "flowering_end": date(2024, 8, 4)}, "overlap"),
    ],
)
def test_invalid_historical_assumptions(overrides: dict, message: str) -> None:
    with pytest.raises(HistoricalInputError, match=message):
        calculate_water_balance(replace(_INPUTS, **overrides), weather_provider=FixtureWeather())


def test_archive_window_is_exact_bounded_and_delayed() -> None:
    assert historical_window(2024, None, None, today=date(2024, 8, 17)) == (
        date(2024, 8, 1),
        date(2024, 8, 10),
    )
    with pytest.raises(HistoricalInputError, match="on or before 2024-08-09"):
        historical_window(2024, None, None, today=date(2024, 8, 16))
    with pytest.raises(HistoricalInputError, match="both start_date"):
        historical_window(2024, _START, None)
    with pytest.raises(HistoricalInputError, match="1 to 62 days"):
        historical_window(2024, date(2024, 1, 1), date(2024, 4, 1))


def _archive_payload() -> dict:
    return {
        "latitude": 41.5,
        "longitude": -93.5,
        "timezone": "America/Chicago",
        "daily_units": {
            "rain_sum": "mm",
            "et0_fao_evapotranspiration": "mm",
            "soil_moisture_0_to_7cm_mean": "m³/m³",
            "soil_moisture_7_to_28cm_mean": "m³/m³",
            "soil_moisture_28_to_100cm_mean": "m³/m³",
        },
        "daily": {
            "time": ["2024-07-31", "2024-08-01", "2024-08-02"],
            "rain_sum": [None, 4, 120],
            "et0_fao_evapotranspiration": [None, 6.75, 5],
            "soil_moisture_0_to_7cm_mean": [0.30, None, None],
            "soil_moisture_7_to_28cm_mean": [0.20, None, None],
            "soil_moisture_28_to_100cm_mean": [0.20, None, None],
        },
    }


def test_open_meteo_uses_era5_with_lookback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPEN_METEO_API_KEY", raising=False)
    requests: list[Request] = []

    def fetch(request: Request, *, timeout: int) -> BytesIO:
        assert timeout > 0
        requests.append(request)
        return BytesIO(json.dumps(_archive_payload()).encode())

    monkeypatch.setattr(historical, "urlopen", fetch)
    provider = OpenMeteoHistoricalWeather()
    maize = calculate_water_balance(_INPUTS, weather_provider=provider)

    assert len(requests) == 1
    endpoint = urlsplit(requests[0].full_url)
    params = parse_qs(endpoint.query)
    assert endpoint.netloc == "archive-api.open-meteo.com"
    assert params["models"] == ["era5"]
    assert params["start_date"] == ["2024-07-31"]
    assert params["end_date"] == ["2024-08-02"]
    assert "apikey" not in params
    assert maize.provenance is not None
    assert maize.provenance.initial_moisture_date == date(2024, 7, 31)


def test_archive_null_weather_is_explicit_and_allows_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _archive_payload()
    payload["daily"]["rain_sum"][1] = None
    monkeypatch.setattr(
        historical, "urlopen", lambda *_args, **_kw: BytesIO(json.dumps(payload).encode())
    )
    provider = OpenMeteoHistoricalWeather()
    with pytest.raises(WeatherUnavailableError, match="missing for 2024-08-01"):
        provider.load(41.5, -93.5, _START, _END)
    payload["daily"]["rain_sum"][1] = 4
    assert provider.load(41.5, -93.5, _START, _END).days[0].rain_mm == 4


def test_customer_api_key_stays_off_public_host_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPEN_METEO_API_KEY", "private-key-test")

    def fail(request: Request, *, timeout: int) -> BytesIO:
        endpoint = urlsplit(request.full_url)
        assert endpoint.netloc == "customer-archive-api.open-meteo.com"
        assert parse_qs(endpoint.query)["apikey"] == ["private-key-test"]
        raise URLError(request.full_url)

    monkeypatch.setattr(historical, "urlopen", fail)
    with pytest.raises(WeatherUnavailableError) as error:
        OpenMeteoHistoricalWeather().load(41.5, -93.5, _START, _END)
    assert "private-key-test" not in str(error.value)
    assert error.value.__suppress_context__
