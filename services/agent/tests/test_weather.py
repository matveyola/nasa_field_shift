"""Weather forecast tool: Open-Meteo values become facts with their source; spraying and frost/heat are fixed rules."""

import datetime as dt

import httpx2

from agent_service.llm.provider import ToolResults
from agent_service.llm.scripted import ScriptedProvider, call, say
from agent_service.llm_agent import LLMAgent
from agent_service.weather import OpenMeteoClient, facts_from_forecast, spray_window

from .conftest import make_client, request_body, schema_validator

NOW = dt.datetime(2026, 11, 10, 12, tzinfo=dt.UTC)


def hour(h: int, *, wind=2.0, gusts=4.0, temp=15.0, rh=60.0, rain=0.0) -> dict:
    return {"hour": h, "wind": wind, "gusts": gusts, "temp": temp, "rh": rh, "rain": rain}


def forecast(days: int = 2, tmin: float = 3.0) -> dict:
    """An Open-Meteo-shaped answer: calm and dry except windy afternoons; the first night at `tmin`."""
    dates = [f"2026-11-{11 + i}" for i in range(days)]
    hours = [f"{d}T{h:02d}:00" for d in dates for h in range(24)]
    return {
        "daily": {"time": dates, "temperature_2m_max": [12.5] * days, "temperature_2m_min": [tmin] + [4.0] * (days - 1),
                  "precipitation_sum": [0.0] * days, "precipitation_probability_max": [10] * days,
                  "wind_speed_10m_max": [6.1] * days, "wind_gusts_10m_max": [11.0] * days},
        "hourly": {"time": hours, "temperature_2m": [14.0] * len(hours), "relative_humidity_2m": [70] * len(hours),
                   "precipitation": [0.0] * len(hours),
                   "wind_speed_10m": [2.0 if int(t[11:13]) < 12 else 6.0 for t in hours],
                   "wind_gusts_10m": [4.0 if int(t[11:13]) < 12 else 11.0 for t in hours]},
    }


def test_spray_window_is_the_longest_calm_dry_stretch_in_working_hours():
    hours = [hour(h, wind=2.0 if 6 <= h < 10 else 6.0) for h in range(24)]
    assert spray_window(hours) == (6, 10)
    hours = [hour(h, rain=1.2 if h == 12 else 0.0) for h in range(24)]  # rain at noon spoils the 6 hours before it
    assert spray_window(hours) == (13, 21)
    assert spray_window([hour(h, wind=8.0) for h in range(24)]) is None


def test_forecast_values_become_facts_with_their_source_and_frost_at_zero():
    facts, days = facts_from_forecast(forecast(tmin=0.0), "uk", NOW)
    by_id = {f.id: f for f in facts}
    tmax = by_id["wx.2026-11-11.temp_max"]
    assert tmax.quantity.value == 12.5 and tmax.quantity.unit == "Cel"
    assert tmax.provenance.kind == "weather_data" and tmax.provenance.source.provider == "Open-Meteo"
    assert by_id["wx.2026-11-11.spray"].category == "05:00–12:00"
    assert by_id["wx.2026-11-11.alert"].category == "можливий заморозок"  # exactly 0 °C counts
    assert "wx.2026-11-12.alert" not in by_id
    assert days[0]["weekday"] == "середа" and days[0]["alerts"] == ["frost"]


def agent_with(handler, script) -> LLMAgent:
    client = OpenMeteoClient("https://api.open-meteo.test/v1/forecast", 5, transport=httpx2.MockTransport(handler))
    return LLMAgent(ScriptedProvider(script), weather=client)


def test_the_agent_reads_the_forecast_for_the_field_and_cites_it():
    asked = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        asked.update(request.url.params)
        return httpx2.Response(200, json=forecast())

    def answer(transcript, _tools):
        result = next(i for i in reversed(transcript) if isinstance(i, ToolResults)).results[0].content
        assert '"weekday": "середа"' in result
        return say("Завтра, у середу, вдень до {{fact:wx.2026-11-11.temp_max}}. Обприскувати найкраще "
                   "{{fact:wx.2026-11-11.spray}}. Це прогноз, він може змінитися; дивіться етикетку препарату.")

    with make_client(agent=agent_with(handler, [call(("get_weather_forecast", {"days": 2})), answer])) as c:
        response = c.post("/v1/agent/message", json=request_body(text="Яка погода завтра?"))
    assert response.status_code == 200, response.text
    message = response.json()
    schema_validator("AgentMessage").validate(message)
    md = message["blocks"][0]["markdown"]
    assert "12,5 °C" in md and "05:00–12:00" in md
    assert any("Open-Meteo" in (f["provenance"].get("source") or {}).get("provider", "") for f in message["facts"])
    assert asked["forecast_days"] == "2" and asked["wind_speed_unit"] == "ms" and "latitude" in asked
    assert {f["provenance"]["kind"] for f in message["facts"]} == {"weather_data", "deterministic_calc"}


def test_no_forecast_means_no_weather_numbers():
    def handler(_request):
        return httpx2.Response(503)

    def answer(transcript, _tools):
        result = next(i for i in reversed(transcript) if isinstance(i, ToolResults)).results[0]
        assert result.is_error and "weather_unavailable" in result.content
        return say("Прогноз погоди зараз недоступний, спробуйте пізніше.")

    with make_client(agent=agent_with(handler, [call(("get_weather_forecast", {"days": 1})), answer])) as c:
        message = c.post("/v1/agent/message", json=request_body(text="Чи буде дощ?")).json()
    assert message["facts"] == [] and "недоступний" in message["blocks"][0]["markdown"]


# ------------------------------------------------------------------ the season so far

import pytest  # noqa: E402

from agent_service.weather import WeatherUnavailable, season_facts  # noqa: E402

START, END = dt.date(2026, 5, 1), dt.date(2026, 5, 3)


def day(high, low, rain, et0):
    return {"temperature_2m_max": high, "temperature_2m_min": low, "precipitation_sum": rain, "et0_fao_evapotranspiration": et0}


def test_degree_days_use_the_crop_base_and_maize_caps_the_day():
    history = {"2026-05-01": day(35.0, 5.0, 2.0, 5.0), "2026-05-02": day(20.0, 12.0, 0.0, 4.0), "2026-05-03": day(9.0, 1.0, 10.0, 2.0)}
    by_id = {f.id: f for f in season_facts(history, START, END, "maize", "uk", NOW)}
    # maize: (30+10)/2-10 = 10, (20+12)/2-10 = 6, (10+10)/2-10 = 0 -> 16
    assert by_id["season.gdd"].quantity.value == 16 and by_id["season.gdd"].quantity.unit == "Cel.d"
    assert by_id["season.rain"].quantity.value == 12.0 and by_id["season.et0"].quantity.value == 11.0
    assert by_id["season.water_balance"].quantity.value == 1.0
    wheat = {f.id: f for f in season_facts(history, START, END, "winter_wheat", "uk", NOW)}
    assert wheat["season.gdd"].quantity.value == round(20 + 16 + 5)  # base 0 °C, no cap
    assert "season.gdd" not in {f.id for f in season_facts(history, START, END, None, "uk", NOW)}


def test_too_few_days_with_data_is_refused_not_summed():
    history = {"2026-05-01": day(20.0, 10.0, 1.0, 3.0), "2026-05-02": day(None, 10.0, 1.0, 3.0)}
    with pytest.raises(WeatherUnavailable):
        season_facts(history, START, END, "maize", "uk", NOW)


def test_the_agent_sums_the_season_from_the_archive_and_the_recent_days():
    today = dt.datetime.now(dt.UTC).date()
    start = today - dt.timedelta(days=10)
    dates = [(start + dt.timedelta(days=i)).isoformat() for i in range(10)]

    def handler(request: httpx2.Request) -> httpx2.Response:
        if "archive" in request.url.host:  # the archive lags: its last 4 days are empty
            vals = lambda v: [v] * 6 + [None] * 4  # noqa: E731
            return httpx2.Response(200, json={"daily": {"time": dates, "temperature_2m_max": vals(20.0), "temperature_2m_min": vals(10.0),
                                                        "precipitation_sum": vals(1.0), "et0_fao_evapotranspiration": vals(2.0)}})
        recent = [(today - dt.timedelta(days=7 - i)).isoformat() for i in range(8)]
        return httpx2.Response(200, json={"daily": {"time": recent, "temperature_2m_max": [20.0] * 8, "temperature_2m_min": [10.0] * 8,
                                                    "precipitation_sum": [1.0] * 8, "et0_fao_evapotranspiration": [2.0] * 8}})

    def answer(transcript, _tools):
        return say("Від сівби випало {{fact:season.rain}}, а випарувалося {{fact:season.et0}}: "
                   "{{fact:season.water_balance}}. Тепла накопичилося {{fact:season.gdd}}.")

    script = [call(("get_season_weather", {"start_date": start.isoformat(), "crop": "maize"})), answer]
    with make_client(agent=agent_with(handler, script)) as c:
        message = c.post("/v1/agent/message", json=request_body(text=f"Скільки дощу від сівби {start.isoformat()}?")).json()
    schema_validator("AgentMessage").validate(message)
    md = message["blocks"][0]["markdown"]
    assert "10 мм" in md and "20 мм" in md and "-10 мм" in md and "50 °C·днів" in md  # 10 days x (15-10)
    assert any("Open-Meteo" in (f["provenance"].get("source") or {}).get("provider", "") for f in message["facts"])


# ------------------------------------------------------------------ soil warm enough to sow

from agent_service.weather import sowing_facts  # noqa: E402

TODAY = dt.date(2026, 4, 20)


def soil(*daily_means: float) -> dict[str, list[float]]:
    """Days from TODAY - 3, each with 24 hourly values at the given mean."""
    return {(TODAY + dt.timedelta(days=i - 3)).isoformat(): [m] * 24 for i, m in enumerate(daily_means)}


def verdict(days, crop="sunflower"):
    return {f.id: f for f in sowing_facts(days, crop, TODAY, "uk", NOW)}


def test_steady_warmth_three_days_in_a_row_against_the_regional_threshold():
    facts = verdict(soil(8.5, 9.0, 8.2, 9.4, 10, 11, 12, 12, 13, 13))  # sunflower: 8 °C, warm for the last 3 days
    assert facts["sowing.ready"].category == "так, ґрунт стійко прогрівся"
    assert facts["sowing.soil_now"].quantity.value == 9.4
    t = facts["sowing.threshold"]
    assert (t.quantity.value, t.quantity.uncertainty.high) == (8.0, 10.0) and "с. 26" in t.provenance.method
    assert t.provenance.source.product_id == "cherkasy-naas-spring-2024"


def test_warmth_coming_in_the_forecast_and_not_at_all():
    later = verdict(soil(6, 7, 7, 7.5, 8.1, 8.4, 9.0, 9, 9, 9))  # warm from TODAY+1: steady on TODAY+3
    assert later["sowing.ready"].category == "ще ні; за прогнозом приблизно з 23.04"
    never = verdict(soil(*[9.5] * 10), crop="soybean")  # soybean needs 10 °C
    assert never["sowing.ready"].category.startswith("ще ні, і за прогнозом до")


def test_the_agent_checks_soil_warmth_through_the_forecast_service():
    """Through the real client: an hourly-only answer must be accepted (it has no daily series)."""
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.params["hourly"] == "soil_temperature_6cm"
        today = dt.datetime.now(dt.UTC).date()
        days = [today + dt.timedelta(days=i - 3) for i in range(10)]
        return httpx2.Response(200, json={"utc_offset_seconds": 0, "hourly": {
            "time": [f"{d.isoformat()}T{h:02d}:00" for d in days for h in range(24)],
            "soil_temperature_6cm": [4.0] * 240}})

    def answer(transcript, _tools):
        return say("Ні: зараз {{fact:sowing.soil_now}}, а треба {{fact:sowing.threshold}}. {{fact:sowing.ready}}.")

    with make_client(agent=agent_with(handler, [call(("check_sowing_soil", {"crop": "soybean"})), answer])) as c:
        message = c.post("/v1/agent/message", json=request_body(text="Чи можна сіяти сою?")).json()
    schema_validator("AgentMessage").validate(message)
    md = message["blocks"][0]["markdown"]
    assert "4 °C" in md and "10 °C (10–12)" in md and "ще ні, і за прогнозом до" in md
    assert "рекомендації Черкаської дослідної станції НААН (2024)" in md


# ------------------------------------------------------------------ crop water need (FAO-56 Kc)

from agent_service.weather import crop_kc  # noqa: E402


def test_the_crop_coefficient_follows_the_fao_curve_and_stops_after_the_season():
    # maize 30/40/50/30 days, Kc 0.3 -> 1.2 -> 0.6
    assert crop_kc(0, "maize") == 0.3 and crop_kc(29, "maize") == 0.3
    assert crop_kc(49, "maize") == pytest.approx(0.75)  # day 50: halfway up the development stage
    assert crop_kc(70, "maize") == 1.2 and crop_kc(119, "maize") == 1.2
    assert crop_kc(149, "maize") == pytest.approx(0.6) and crop_kc(150, "maize") is None


def test_crop_water_need_is_grass_evaporation_times_the_coefficient():
    history = {"2026-05-01": day(20.0, 10.0, 2.0, 5.0), "2026-05-02": day(20.0, 10.0, 0.0, 4.0), "2026-05-03": day(20.0, 10.0, 1.0, 3.0)}
    by_id = {f.id: f for f in season_facts(history, START, END, "soybean", "uk", NOW)}
    assert by_id["season.crop_water_use"].quantity.value == round(0.4 * 12, 1)  # initial stage, Kc 0.4
    assert by_id["season.crop_water_balance"].quantity.value == round(3.0 - 4.8, 1)
    assert "таблиць 11 і 12" in by_id["season.crop_water_use"].provenance.method
    assert "season.crop_water_use" not in {f.id for f in season_facts(history, START, END, None, "uk", NOW)}


def test_every_crop_water_note_fits_the_contract_even_after_the_season():
    start = dt.date(2025, 9, 1)
    history = {(start + dt.timedelta(days=i)).isoformat(): day(20.0, 10.0, 1.0, 3.0) for i in range(340)}
    end = start + dt.timedelta(days=339)
    for crop in ("maize", "winter_wheat", "sunflower", "soybean"):
        for lang in ("uk", "en"):
            by_id = {f.id: f for f in season_facts(history, start, end, crop, lang, NOW)}  # validates the 300-char limit
            assert by_id["season.crop_water_use"].quantity.value > 0


def test_crop_values_after_the_season_carry_the_seasons_own_dates():
    start = dt.date(2026, 5, 10)
    history = {(start + dt.timedelta(days=i)).isoformat(): day(20.0, 10.0, 1.0, 3.0) for i in range(180)}
    by_id = {f.id: f for f in season_facts(history, start, start + dt.timedelta(days=179), "soybean", "uk", NOW)}
    assert by_id["season.rain"].quantity.value == 180.0 and by_id["season.crop_rain"].quantity.value == 140.0
    crop = by_id["season.crop_water_balance"]
    assert crop.provenance.temporal.end == start + dt.timedelta(days=139)  # soybean: 140 days in Table 11
    assert crop.quantity.value == round(140.0 - by_id["season.crop_water_use"].quantity.value, 1)
