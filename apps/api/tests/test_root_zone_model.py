from datetime import date, timedelta
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from field_shift_science.geo_ml import ifs
from field_shift_science.geo_ml.era5_land import MoistureRequestRejected
from field_shift_science.geo_ml.forecast_cache import ForecastCache
from field_shift_science.simulation.root_zone_inputs import (
    fetch_ifs_root_zone,
    root_zone_storage_mm,
)
from field_shift_science.simulation.root_zone_model import (
    RootZoneCorrection,
    get_root_zone_correction,
    reference_reserve_mm,
    root_zone_version,
)
from field_shift_science.weather.historical import WeatherUnavailableError
from pydantic import ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError


def test_reference_reserve_is_bounded_by_model_capacity():
    assert reference_reserve_mm(260) == pytest.approx(110)
    assert reference_reserve_mm(500) == pytest.approx(150)


def forecast_payload():
    init = date(2026, 11, 8)
    series = {
        "soil_moisture_0_to_7cm_mean": (0.30, "m³/m³"),
        "soil_moisture_7_to_28cm_mean": (0.35, "m³/m³"),
        "soil_moisture_28_to_100cm_mean": (0.40, "m³/m³"),
        "soil_temperature_0_to_7cm_mean": (9, "°C"),
        "temperature_2m_mean": (8, "°C"),
        "precipitation_sum": (2, "mm"),
        "rain_sum": (0.1, "mm"),
        "et0_fao_evapotranspiration": (1.8, "mm"),
    }
    return init, {
        "latitude": 41.705785,
        "longitude": -93.409033,
        "utc_offset_seconds": 0,
        "daily_units": {key: unit for key, (_, unit) in series.items()},
        "daily": {
            "time": [(init + timedelta(days=index)).isoformat() for index in range(6)],
            **{key: [value] * 6 for key, (value, _) in series.items()},
        },
    }


def test_ifs_root_zone_reuses_pinned_forecast_and_uses_liquid_rain(monkeypatch):
    monkeypatch.delenv("OPEN_METEO_API_KEY", raising=False)
    init, payload = forecast_payload()

    def fetch(url):
        request = parse_qs(urlsplit(url).query)
        assert request["run"] == ["2026-11-08T00:00"]
        assert "rain_sum" in request["daily"][0].split(",")
        assert "soil_moisture_28_to_100cm_mean" in request["daily"][0].split(",")
        return payload

    result = fetch_ifs_root_zone(41.705785, -93.409033, init, fetch=fetch)
    assert result.initial_layers == (0.3, 0.35, 0.4)
    assert result.model_cycle == "50R1"
    assert len(result.days) == 5 and result.days[0].date == init + timedelta(days=1)
    assert result.days[0].rain_mm == 0.1
    assert root_zone_storage_mm(result.initial_layers, 1) == pytest.approx(382.5)
    assert root_zone_storage_mm(result.initial_layers, 0.6) == pytest.approx(222.5)

    payload["daily"]["soil_moisture_28_to_100cm_mean"][-1] = None
    with pytest.raises(WeatherUnavailableError, match="incomplete"):
        fetch_ifs_root_zone(41.705785, -93.409033, init, fetch=fetch)


def test_surface_and_water_share_complete_ifs_inputs_for_the_exact_location(monkeypatch):
    init, payload = forecast_payload()
    redis = Mock()
    data = {}
    redis.get.side_effect = data.get
    redis.set.side_effect = lambda key, value, *, ex: data.__setitem__(key, value)
    monkeypatch.setattr(ifs, "shared_forecast_cache", lambda: ForecastCache(redis))
    fetch = Mock(return_value=payload)
    monkeypatch.setattr(ifs, "_fetch", fetch)
    location = (41.705785, -93.409033)

    surface = ifs.fetch_ifs_run(*location, init)
    water = fetch_ifs_root_zone(*location, init)
    assert fetch.call_count == 1
    assert redis.set.call_args.kwargs["ex"] == 48 * 3600
    assert surface.days[0].precipitation_mm == 2
    assert water.days[0].rain_mm == 0.1

    ifs.fetch_ifs_run(location[0] + 0.001, location[1], init)
    assert fetch.call_count == 2


def test_incomplete_root_zone_inputs_are_never_cached(monkeypatch):
    init, payload = forecast_payload()
    redis = Mock()
    redis.get.return_value = None
    monkeypatch.setattr(ifs, "shared_forecast_cache", lambda: ForecastCache(redis))
    fetch = Mock(return_value=payload)
    monkeypatch.setattr(ifs, "_fetch", fetch)
    location = (41.705785, -93.409033)
    payload["daily"][ifs.MIDDLE_MOISTURE][0] = None

    assert ifs.fetch_ifs_run(*location, init).initial_moisture == 0.3
    with pytest.raises(WeatherUnavailableError, match="incomplete"):
        fetch_ifs_root_zone(*location, init)
    redis.set.assert_not_called()

    payload["daily"][ifs.MIDDLE_MOISTURE][0] = 0.35
    assert fetch_ifs_root_zone(*location, init).initial_layers == (0.3, 0.35, 0.4)
    redis.set.assert_called_once()


def test_failed_cache_write_does_not_repeat_the_provider_request(monkeypatch):
    init, payload = forecast_payload()
    redis = Mock()
    redis.get.return_value = None
    redis.set.side_effect = RedisConnectionError()
    monkeypatch.setattr(ifs, "shared_forecast_cache", lambda: ForecastCache(redis))
    fetch = Mock(return_value=payload)
    monkeypatch.setattr(ifs, "_fetch", fetch)

    assert fetch_ifs_root_zone(41.705785, -93.409033, init).initial_layers == (0.3, 0.35, 0.4)
    fetch.assert_called_once()
    redis.lock.return_value.release.assert_called_once()


def test_surface_falls_back_if_extended_ifs_request_is_rejected(monkeypatch):
    init, payload = forecast_payload()
    redis = Mock()
    redis.get.return_value = None
    monkeypatch.setattr(ifs, "shared_forecast_cache", lambda: ForecastCache(redis))
    fetch = Mock(side_effect=[MoistureRequestRejected(), payload])
    monkeypatch.setattr(ifs, "_fetch", fetch)

    assert ifs.fetch_ifs_run(41.705785, -93.409033, init).initial_moisture == 0.3
    extended, fallback = (call.args[0] for call in fetch.call_args_list)
    assert ifs.DEEP_MOISTURE in extended
    assert ifs.DEEP_MOISTURE not in fallback
    redis.set.assert_not_called()


def test_bundled_model_enforces_production_gates():
    model = get_root_zone_correction()
    assert model is not None
    assert model.validation["qualified_farms"]["sites"] >= 2
    assert model.promoted == all(model.validation["gates"].values())
    payload = model.model_dump(mode="json", exclude={"version"})
    payload["promoted"] = True
    payload["validation"]["gates"]["improves_raw_ifs_rmse"] = False
    with pytest.raises(ValidationError, match="lacks production validation"):
        RootZoneCorrection.model_validate({"version": root_zone_version(payload), **payload})
