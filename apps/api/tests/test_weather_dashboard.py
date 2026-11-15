"""Synthetic weather responses exercise the source and period contracts."""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from field_shift.api import create_app
from field_shift.config import Settings
from field_shift.db import build_session_factory
from field_shift.models import metadata
from field_shift.modules.field_weather import dashboard
from field_shift.seed import seed_demo

NOW = datetime(2026, 11, 15, 12, tzinfo=UTC)
TODAY = NOW.date()
DAY = timedelta(days=1)
GEOMETRY = {"type": "Polygon", "coordinates": [[[30, 50], [30.01, 50], [30.01, 50.01], [30, 50]]]}


def payload(start, end, *, forecast_hole=None, invalid_units=False):
    days = [start + i * DAY for i in range((end - start).days + 1)]
    return dashboard.ProviderDaily.model_validate(
        {
            "utc_offset_seconds": 0,
            "daily": {
                "time": [day.isoformat() for day in days],
                "precipitation_sum": [
                    None if day == forecast_hole else i for i, day in enumerate(days)
                ],
                "temperature_2m_min": [-2] * len(days),
                "temperature_2m_max": [11] * len(days),
                "et0_fao_evapotranspiration": [2] * len(days),
                "soil_moisture_0_to_7cm_mean": [0.34] * len(days),
            },
            "daily_units": {
                "precipitation_sum": "inch" if invalid_units else "mm",
                "temperature_2m_min": "°C",
                "temperature_2m_max": "°C",
                "et0_fao_evapotranspiration": "mm",
                "soil_moisture_0_to_7cm_mean": "m³/m³",
            },
        }
    )


def build(start, end):
    return dashboard.build("field", 3, GEOMETRY, start=start, end=end, now=NOW)


def test_daily_sources_aggregate_climate_only_on_uncovered_dates(monkeypatch):
    calls = []

    def provider(source, lat, lon, start, end, slot):
        calls.append((source, start, end))
        assert 50 <= lat <= 50.01
        assert 30 <= lon <= 30.01
        return payload(start, end, forecast_hole=TODAY + 2 * DAY if source == "forecast" else None)

    monkeypatch.setattr(dashboard, "_provider", provider)
    monkeypatch.setattr(
        dashboard, "_surface_provider", lambda lat, lon, start, end, slot: payload(start, end)
    )
    result = build(TODAY - 8 * DAY, TODAY + 25 * DAY)
    assert result.field_id == "field"
    assert result.boundary_revision == 3
    rain = result.series["precipitation"]
    assert [(span.source, span.reason, len(span.points)) for span in rain] == [
        ("reanalysis", None, 2),
        (None, "archive_lag", 0),
        ("forecast", None, 2),
        ("climate", None, 1),
        ("forecast", None, 13),
        ("climate", None, 2),
    ]
    assert rain[2].points[0].value == 0
    climates = rain[-1].points
    assert climates[0].start == TODAY + 16 * DAY
    assert climates[0].end == TODAY + 22 * DAY
    assert climates[0].value == 17
    assert climates[1].value == 22
    assert result.series["reference_et0"][-1].reason == "unsupported"
    assert result.series["surface_moisture"][0].points[0].value == 0.34
    assert result.series["surface_moisture"][-1].reason == "unsupported"
    assert calls == [
        ("reanalysis", TODAY - 8 * DAY, TODAY - 7 * DAY),
        ("forecast", TODAY, TODAY + 15 * DAY),
        ("climate", TODAY + 2 * DAY, TODAY + 25 * DAY),
    ]


def test_failed_forecast_uses_climate_and_does_not_fill_recent_history_or_et0(monkeypatch):
    def provider(source, lat, lon, start, end, slot):
        if source != "climate":
            raise dashboard.WeatherUnavailableError("fixture failure")
        return payload(start, end)

    monkeypatch.setattr(dashboard, "_provider", provider)
    result = build(TODAY - DAY, TODAY + 10 * DAY)
    assert result.series["precipitation"][0].reason == "archive_lag"
    assert result.series["precipitation"][1].start == TODAY
    assert result.series["precipitation"][1].source == "climate"
    assert result.series["temperature_min"][1].points[0].value == -2
    assert result.series["reference_et0"][1].points == []
    assert result.series["reference_et0"][1].reason == "provider_gap"


def test_bad_units_and_unsupported_dates_do_not_create_values(monkeypatch):
    monkeypatch.setattr(
        dashboard,
        "_provider",
        lambda source, lat, lon, start, end, slot: payload(start, end, invalid_units=True),
    )
    result = build(TODAY + 30 * DAY, TODAY + 32 * DAY)
    assert result.series["precipitation"][0].source is None
    assert result.series["precipitation"][0].reason == "provider_gap"
    assert result.series["temperature_max"][0].points[0].value == 11
    outside = build(date(2050, 1, 1), date(2050, 1, 2))
    assert outside.series["precipitation"][0].reason == "outside_coverage"
    early = build(date(1939, 12, 30), date(1939, 12, 31))
    assert early.series["surface_moisture"][0].reason == "outside_coverage"


def test_dashboard_rejects_unknown_field_stale_revision_and_bad_period_before_io(monkeypatch):
    factory = build_session_factory("sqlite://")
    metadata.create_all(factory.kw["bind"])
    try:
        with factory() as session:
            seed_demo(session)
            session.commit()
        monkeypatch.setattr(
            dashboard, "_provider", lambda *_: pytest.fail("Unexpected provider call")
        )
        app = create_app(Settings(), session_factory=factory)
        with TestClient(app) as client:
            query = {"start": "2026-06-01", "end": "2026-06-02", "geometry_revision": 0}
            path = "/api/fields/F-01/weather-dashboard"
            assert (
                client.get("/api/fields/invalid/weather-dashboard", params=query).status_code == 404
            )
            assert client.get(path, params={**query, "geometry_revision": 99}).status_code == 409
            assert (
                client.get(
                    path, params={**query, "end": "2026-06-03", "start": "2026-06-10"}
                ).status_code
                == 422
            )
            assert client.get(path, params={**query, "end": "2026-10-01"}).status_code == 422
            forecast_job = {
                "kind": "simulation",
                "field_id": "F-01",
                "inputs": {
                    "season": 2026,
                    "scenario": "baseline",
                    "source": "forecast",
                    "analysis": "water_forecast",
                },
            }
            assert (
                client.post(
                    "/api/jobs?geometry_revision=99",
                    json=forecast_job,
                    headers={"X-Field-Shift-Request": "1"},
                ).status_code
                == 409
            )
            assert client.get("/api/jobs?field_id=F-01").json() == []
            assert (
                client.get(
                    "/api/water/forecast", params={"field_id": "F-01", "geometry_revision": 99}
                ).status_code
                == 409
            )
            assert (
                client.get(
                    "/api/water/forecast", params={"field_id": "F-01", "geometry_revision": 0}
                ).status_code
                == 200
            )
            response = client.get(
                path, params={**query, "start": "1939-06-01", "end": "1939-06-02"}
            )
            assert response.status_code == 200
            assert response.json()["series"]["precipitation"][0]["reason"] == "outside_coverage"
    finally:
        factory.kw["bind"].dispose()
