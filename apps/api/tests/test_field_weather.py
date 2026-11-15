"""Deterministic provider fixtures; actual network probes are separate evidence."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Event
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from field_shift.api import create_app
from field_shift.config import Settings
from field_shift.db import build_session_factory
from field_shift.models import metadata
from field_shift.modules.field_weather import providers, service
from field_shift.modules.field_weather.calculations import (
    days,
    forecast_periods,
    power_days,
    rainfall_period,
)
from field_shift.modules.fields.models import Field
from field_shift.seed import seed_demo

NOW = datetime(2026, 11, 15, 12, 30, tzinfo=UTC)
GEOMETRY = {"type": "Polygon", "coordinates": [[[30, 50], [30.01, 50], [30.01, 50.01], [30, 50]]]}


def power(values, fill=-999):
    return {
        "header": {"fill_value": fill, "time_standard": "UTC"},
        "parameters": {"PRECTOTCORR": {"units": "mm/day"}},
        "properties": {
            "parameter": {"PRECTOTCORR": {d.strftime("%Y%m%d"): v for d, v in values.items()}}
        },
    }


def forecast(now=NOW, timezone="Europe/Kyiv"):
    zone = ZoneInfo(timezone)
    start = datetime.combine(now.astimezone(zone).date(), datetime.min.time(), zone)
    hours = [now.replace(minute=0) + timedelta(hours=i) for i in range(30)]
    return {
        "timezone": timezone,
        "hourly_units": {
            "time": "unixtime",
            "temperature_2m": "°C",
            "precipitation": "mm",
            "wind_gusts_10m": "m/s",
        },
        "daily_units": {
            "time": "unixtime",
            "precipitation_sum": "mm",
            "precipitation_probability_max": "%",
        },
        "hourly": {
            "time": [int(t.timestamp()) for t in hours],
            "temperature_2m": [-1, 0, -2] + [5] * 27,
            "precipitation": [0] * 30,
            "wind_gusts_10m": [8] * 30,
        },
        "daily": {
            "time": [int((start + timedelta(days=i)).timestamp()) for i in range(7)],
            "precipitation_sum": [99, 1, 2, 3, 3, 1, 99],
            "precipitation_probability_max": [99, 20, 10, 40, 10, 30, 99],
        },
    }


def test_current_period_exact_sum_zero_fill_and_internal_gap():
    end = date(2026, 11, 13)
    values = {d: 2.0 for d in days(end - timedelta(days=29), end)}
    values[end] = 0
    parsed = power_days(power(values))
    assert rainfall_period(parsed, end, 7)["rain_mm"] == 12
    assert rainfall_period(parsed, end, 30)["rain_mm"] == 58
    values[end - timedelta(days=3)] = -88
    parsed = power_days(power(values, fill=-88))
    assert parsed[end] == 0 and parsed[end - timedelta(days=3)] is None
    result = rainfall_period(parsed, end, 7, parsed)
    assert result["rain_mm"] is None and result["difference_mm"] is None
    assert result["missing_dates"] == [end - timedelta(days=3)]


def test_typical_is_median_of_complete_equal_length_periods_not_daily_mean():
    end = date(2026, 11, 13)
    current = {d: 3.0 for d in days(end - timedelta(days=29), end)}
    history = {}
    for i, year in enumerate(range(2016, 2026), 1):
        anchor = end.replace(year=year)
        history.update({d: float(i) for d in days(anchor - timedelta(days=29), anchor)})
    # Both 7- and 30-day samples for 2016 become incomplete, without sliding the anchor.
    del history[date(2016, 11, 12)]
    seven = rainfall_period(current, end, 7, history)
    thirty = rainfall_period(current, end, 30, history)
    assert len(seven["typical_periods"]) == 9
    assert seven["typical_rain_mm"] == 42 and seven["difference_mm"] == -21
    assert thirty["typical_rain_mm"] == 180 and thirty["difference_mm"] == -90
    assert all((s["end"] - s["start"]).days == 29 for s in thirty["typical_periods"])
    current[end - timedelta(days=2)] = None
    assert rainfall_period(current, end, 7, history)["difference_mm"] is None


@pytest.mark.parametrize("length", [7, 30])
def test_february_29_anchor_clamps_but_period_length_does_not_change(length):
    end = date(2028, 2, 29)
    history = {d: 1 for d in days(date(2018, 1, 1), date(2027, 12, 31))}
    result = rainfall_period({}, end, length, history)
    assert len(result["typical_periods"]) == 10
    assert result["typical_rain_mm"] == length
    assert all((s["end"] - s["start"]).days == length - 1 for s in result["typical_periods"])
    assert any(s["end"] == date(2024, 2, 29) for s in result["typical_periods"])
    assert any(s["end"] == date(2027, 2, 28) for s in result["typical_periods"])


def test_forecast_24_future_hours_and_tomorrow_five_days_keep_all_ties():
    result = forecast_periods(forecast(), NOW)
    assert len(result["hours"]) == 24 and result["hours_complete"]
    assert result["hours"][0]["time"].hour == 15  # Kyiv, first future hour.
    assert result["hours"][0]["below_zero"] is False  # zero is not frost.
    assert result["hours"][1]["below_zero"] is True
    assert result["forecast_rain_mm"] == 10
    assert result["wettest_dates"] == [date(2026, 11, 18), date(2026, 11, 19)]
    assert result["lowest_probability_dates"] == [date(2026, 11, 17), date(2026, 11, 19)]
    assert result["requested_start"] == date(2026, 11, 16)


def test_forecast_missing_is_not_dry_or_zero_probability():
    payload = forecast()
    payload["daily"]["precipitation_sum"][2] = None
    payload["daily"]["precipitation_probability_max"][4] = None
    payload["hourly"]["wind_gusts_10m"][3] = None
    result = forecast_periods(payload, NOW)
    assert result["forecast_rain_mm"] is None and result["wettest_dates"] == []
    assert result["lowest_probability_dates"] == [] and not result["hours_complete"]
    payload["daily"]["time"].pop()
    payload["daily"]["time"].pop()
    result = forecast_periods(payload, NOW)
    assert len(result["forecast_days"]) == 4 and result["missing_dates"] == [date(2026, 11, 20)]


def test_timezone_uses_field_day_and_dst_actual_instants():
    now = datetime(2026, 11, 1, 4, 30, tzinfo=UTC)  # New York DST transition.
    result = forecast_periods(forecast(now, "America/New_York"), now)
    assert len(result["hours"]) == 24
    assert result["requested_start"] == date(2026, 11, 2)
    assert len({h["time"].timestamp() for h in result["hours"]}) == 24
    payload = forecast()
    payload["hourly_units"]["wind_gusts_10m"] = "km/h"
    with pytest.raises(ValueError, match="units"):
        forecast_periods(payload, NOW)


def test_archive_lag_does_not_skip_internal_holes_or_read_future(monkeypatch):
    end = date(2026, 11, 12)
    values = {d: 1 for d in days(date(2026, 9, 1), end)}
    values[date(2026, 11, 14)] = -999  # Recent archive not yet available.
    values[date(2026, 11, 15)] = 999  # Uncompleted day must be excluded.
    values[date(2026, 11, 10)] = -999  # Internal hole remains in both periods.
    calls = []

    def archive(lat, lon, start, finish, historical=False):
        calls.append((start, finish, historical))
        return power(values), "https://example.test/archive", NOW - timedelta(hours=1)

    monkeypatch.setattr(providers, "archive", archive)
    result = service.history("no-crop", 4, GEOMETRY, now=NOW)
    assert result.available_end == end and result.archive_lag_days == 2
    assert result.boundary_revision == 4
    assert all(p.rain_mm is None and p.difference_mm is None for p in result.periods)
    assert calls == [(date(2026, 9, 16), date(2026, 11, 14), False)]
    assert result.sources[0].retrieved_at == NOW - timedelta(hours=1)


def test_typical_provider_query_bounded_and_excludes_current_year(monkeypatch):
    calls = []

    def archive(lat, lon, start, finish, historical=False):
        calls.append((start, finish, historical))
        return power({d: 0 for d in days(start, finish)}), "https://example.test/archive", NOW

    monkeypatch.setattr(providers, "archive", archive)
    result = service.history("field", 0, GEOMETRY, typical=True, now=NOW)
    assert len(calls) == 2 and calls[1][2]
    assert calls[1][1] == date(2025, 11, 14)
    assert calls[1][0] == date(2016, 10, 16)
    assert all(p.typical_rain_mm == 0 and p.difference_mm == 0 for p in result.periods)
    assert all(len(p.typical_periods) == 10 for p in result.periods)


def test_same_key_requests_coalesce_and_preserve_retrieval_time(monkeypatch):
    providers._CACHE.clear()
    entered, release = Event(), Event()
    calls = []

    def read(url):
        calls.append(url)
        entered.set()
        assert release.wait(3)
        return {"data": "test"}

    monkeypatch.setattr(providers, "read_json", read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(providers.cached_json, "test://same", 100)
        assert entered.wait(3)
        b = pool.submit(providers.cached_json, "test://same", 100)
        release.set()
        assert a.result() == b.result()
    assert calls == ["test://same"] and not providers._FLIGHTS
    assert providers.cached_json("test://same", 100) == a.result()


def test_provider_retry_is_bounded_and_permanent_errors_not_retried(monkeypatch):
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(1)
        raise HTTPError("https://example.test", 503, "down", {}, None)

    monkeypatch.setattr(providers, "urlopen", unavailable)
    with pytest.raises(providers.WeatherUnavailable):
        providers.read_json("https://example.test")
    assert len(calls) == 2

    def permanent(*args, **kwargs):
        calls.append(1)
        raise HTTPError("https://example.test", 400, "bad", {}, None)

    monkeypatch.setattr(providers, "urlopen", permanent)
    with pytest.raises(providers.WeatherUnavailable):
        providers.read_json("https://example.test")
    assert len(calls) == 3


def test_archive_unit_contract_rejects_local_solar_days():
    payload = power({date(2026, 11, 1): 1})
    payload["header"]["time_standard"] = "LST"
    with pytest.raises(ValueError, match="UTC"):
        power_days(payload)


def test_owned_route_unknown_crop_missing_field_and_auth(monkeypatch):
    factory = build_session_factory("sqlite://")
    metadata.create_all(factory.kw["bind"])
    with factory() as session:
        seed_demo(session)
        field = session.get(Field, "F-01")
        field.crop = "Unknown"
        session.commit()
    monkeypatch.setattr(providers, "forecast", lambda *_: (forecast(), "https://example.test", NOW))
    monkeypatch.setattr(
        service, "datetime", type("Clock", (), {"now": staticmethod(lambda _: NOW)})
    )
    app = create_app(Settings(), session_factory=factory)
    with TestClient(app) as client:
        response = client.get("/api/fields/F-01/weather-summary")
        assert response.status_code == 200, response.text
        assert response.json()["hours"][0]["temperature_c"] == 0
        assert client.get("/api/fields/missing/weather-summary").status_code == 404
        monkeypatch.setattr(
            providers,
            "forecast",
            lambda *_: (_ for _ in ()).throw(
                providers.WeatherUnavailable("temporarily unavailable")
            ),
        )
        assert client.get("/api/fields/F-01/weather-summary").status_code == 503
    app = create_app(Settings(access_mode="team"), session_factory=factory)
    with TestClient(app) as client:
        assert client.get("/api/fields/F-01/weather-summary").status_code == 401
    factory.kw["bind"].dispose()


def test_empty_archive_does_not_invent_zero_or_download_a_baseline(monkeypatch):
    calls = []

    def archive(*args):
        calls.append(args)
        return power({date(2026, 11, 14): -999}), "https://example.test", NOW

    monkeypatch.setattr(providers, "archive", archive)
    result = service.history("field", 0, GEOMETRY, typical=True, now=NOW)
    assert result.available_end is None and result.periods == []
    assert result.archive_lag_days is None and len(calls) == 1


def test_different_provider_requests_respect_global_concurrency_bound(monkeypatch):
    providers._CACHE.clear()
    occupied = [providers._SLOTS.acquire(blocking=False) for _ in range(2)]
    assert all(occupied)
    try:
        monkeypatch.setattr(providers, "read_json", lambda _: pytest.fail("No slot, no network"))
        with pytest.raises(providers.WeatherUnavailable, match="busy"):
            providers.cached_json("test://busy", 1)
        assert not providers._FLIGHTS
    finally:
        for _ in occupied:
            providers._SLOTS.release()


def test_foreign_field_cannot_trigger_provider_calls(monkeypatch):
    from field_shift.modules.farm.models import Farm

    factory = build_session_factory("sqlite://")
    metadata.create_all(factory.kw["bind"])
    with factory() as session:
        seed_demo(session)
        session.add(
            Farm(
                id=2,
                name="Other owner",
                season=2026,
                next_field_number=2,
                irrigation_available=False,
                priority="water",
            )
        )
        session.flush()
        session.add(
            Field(
                id="private",
                farm_id=2,
                name="Private",
                crop="Unknown",
                season=2026,
                area_ha=1,
                geometry=GEOMETRY,
                boundary_revision=0,
            )
        )
        session.commit()
    monkeypatch.setattr(providers, "forecast", lambda *_: pytest.fail("Foreign provider call"))
    monkeypatch.setattr(providers, "archive", lambda *_: pytest.fail("Foreign provider call"))
    app = create_app(Settings(), session_factory=factory)
    with TestClient(app) as client:
        assert client.get("/api/fields/private/weather-summary").status_code == 404
        assert client.get("/api/fields/private/rainfall-history?typical=true").status_code == 404
    factory.kw["bind"].dispose()
