import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from io import BytesIO
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest
from fastapi.testclient import TestClient

from field_shift.api import create_app
from field_shift.db import build_session_factory
from field_shift.models import metadata
from field_shift.modules.weather import historical, routes, seasonal
from field_shift.seed import seed_demo


@pytest.fixture
def client() -> Iterator[TestClient]:
    factory = build_session_factory("sqlite://")
    engine = factory.kw["bind"]
    metadata.create_all(engine)
    with factory() as session:
        seed_demo(session)
    seasonal._archive.cache_clear()
    seasonal._forecast.cache_clear()
    try:
        with TestClient(create_app(session_factory=factory)) as test_client:
            yield test_client
    finally:
        seasonal._archive.cache_clear()
        seasonal._forecast.cache_clear()
        engine.dispose()


def test_seasonal_weather_route_uses_selected_field_and_retains_gaps(
    monkeypatch, client: TestClient
) -> None:

    calls: list[dict] = []

    def archive(request: Request, *, timeout: int) -> BytesIO:
        calls.append(parse_qs(urlsplit(request.full_url).query))
        assert timeout == 12
        return BytesIO(
            json.dumps(
                {
                    "latitude": 42.75,
                    "longitude": -89.75,
                    "timezone": "America/Chicago",
                    "daily_units": {
                        "temperature_2m_min": "°C",
                        "temperature_2m_max": "°C",
                        "precipitation_sum": "mm",
                    },
                    "daily": {
                        "time": ["2026-01-01", "2026-01-03"],
                        "temperature_2m_min": [-4, 5],
                        "temperature_2m_max": [3, None],
                        "precipitation_sum": [0, 2],
                    },
                }
            ).encode()
        )

    def fixed_load(field_id, revision, geometry, season):
        return seasonal.load_seasonal_weather(
            field_id, revision, geometry, season, today=date(2026, 1, 10)
        )

    monkeypatch.setattr(historical, "urlopen", archive)
    monkeypatch.setattr(routes, "load_seasonal_weather", fixed_load)
    response = client.get("/api/fields/F-01/seasonal-weather?season=2026")
    assert response.status_code == 200
    result = response.json()
    assert result["field_id"] == "F-01"
    assert result["season"] == 2026
    assert result["boundary_revision"] == 0
    assert result["source"] == "ECMWF ERA5"
    assert result["timezone"] == "America/Chicago"
    assert result["days"] == [
        {
            "date": "2026-01-01",
            "temperature_min_c": -4,
            "temperature_max_c": 3,
            "precipitation_mm": 0,
        },
        {
            "date": "2026-01-02",
            "temperature_min_c": None,
            "temperature_max_c": None,
            "precipitation_mm": None,
        },
        {
            "date": "2026-01-03",
            "temperature_min_c": 5,
            "temperature_max_c": None,
            "precipitation_mm": 2,
        },
    ]
    assert client.get("/api/fields/F-01/seasonal-weather?season=2026").json() == result
    assert client.get("/api/fields/F-01/seasonal-weather?season=2027").json() is None
    assert client.get("/api/fields/unknown/seasonal-weather?season=2026").status_code == 404
    assert client.get("/api/fields/F-01/seasonal-weather?season=1900").status_code == 422
    assert len(calls) == 1
    assert calls[0]["models"] == ["era5"]
    assert calls[0]["timezone"] == ["auto"]
    assert calls[0]["start_date"] == ["2026-01-01"]
    assert calls[0]["end_date"] == ["2026-01-03"]


def test_seasonal_weather_rejects_missing_polygon_before_fetch() -> None:
    with pytest.raises(historical.WeatherUnavailableError, match="valid field polygon"):
        seasonal.load_seasonal_weather("F-01", 0, None, 2024, today=date(2025, 1, 9))


def test_field_forecast_keeps_missing_days_and_validates_freshness(
    monkeypatch, client: TestClient
) -> None:
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 10, 14, tzinfo=UTC)

    monkeypatch.setattr(seasonal, "datetime", Clock)
    calls = []
    first_day = "2026-11-10"

    def forecast(request: Request, *, timeout: int) -> BytesIO:
        url = urlsplit(request.full_url)
        assert url.path == "/v1/forecast"
        calls.append(parse_qs(url.query))
        return BytesIO(
            json.dumps(
                {
                    "latitude": 42.75,
                    "longitude": -89.75,
                    "timezone": "America/Chicago",
                    "daily_units": {
                        "temperature_2m_min": "°C",
                        "temperature_2m_max": "°C",
                        "precipitation_sum": "mm",
                    },
                    "daily": {
                        "time": [first_day],
                        "temperature_2m_min": [-4],
                        "temperature_2m_max": [3],
                        "precipitation_sum": [0],
                    },
                }
            ).encode()
        )

    monkeypatch.setattr(historical, "urlopen", forecast)
    response = client.get("/api/fields/F-01/weather-forecast")
    assert response.status_code == 200
    result = response.json()
    assert result["source"] == "Open-Meteo Best Match"
    assert result["fetched_at"] == "2026-11-10T14:00:00Z"
    assert result["start_date"] == "2026-11-10"
    assert result["end_date"] == "2026-11-16"
    assert len(result["days"]) == 7
    assert result["days"][0]["precipitation_mm"] == 0
    assert result["days"][1]["precipitation_mm"] is None
    assert client.get("/api/fields/F-01/weather-forecast").json() == result
    assert client.get("/api/fields/unknown/weather-forecast").status_code == 404
    assert len(calls) == 1
    assert calls[0]["forecast_days"] == ["7"]
    assert calls[0]["timezone"] == ["auto"]
    assert "models" not in calls[0]

    seasonal._forecast.cache_clear()
    first_day = "2026-11-04"
    assert client.get("/api/fields/F-01/weather-forecast").status_code == 503
