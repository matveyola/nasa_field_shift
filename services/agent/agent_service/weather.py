"""Weather forecast for the selected field (Open-Meteo), and the plain rules built on it.

Every number comes from the forecast as distributed, as a Fact with its provenance; the language model only words
them. Spraying windows and frost / heat notices are fixed rules applied to the same forecast (deterministic, never
the model). Their thresholds are rough, general ones (unverified against product labels), so every answer must send
the farmer to the product label and local rules.

Open-Meteo: free for non-commercial use, data under CC BY 4.0 (attribution: the source name is on every value).
No API key. https://open-meteo.com/en/terms
"""

import datetime as dt
from typing import Any

import httpx2

from contracts.models import Fact, Provenance, Quantity, Source, Temporal

DAILY = ("temperature_2m_max", "temperature_2m_min", "precipitation_sum", "precipitation_probability_max",
         "wind_speed_10m_max", "wind_gusts_10m_max")
HOURLY = ("temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m", "wind_gusts_10m")

# daily variable -> (fact name, label key, unit, aggregation)
_DAILY_FACTS = {
    "temperature_2m_max": ("temp_max", "fact.forecast_temp_max", "Cel", "daily maximum"),
    "temperature_2m_min": ("temp_min", "fact.forecast_temp_min", "Cel", "daily minimum"),
    "precipitation_sum": ("rain", "fact.forecast_rain", "mm", "daily sum"),
    "precipitation_probability_max": ("rain_chance", "fact.forecast_rain_chance", "%", "daily maximum"),
    "wind_speed_10m_max": ("wind", "fact.forecast_wind_max", "m/s", "daily maximum at 10 m"),
    "wind_gusts_10m_max": ("gusts", "fact.forecast_wind_gust", "m/s", "daily maximum at 10 m"),
}

# ponytail: one general rule set for all crops and products; per-product label limits if this is used for real.
SPRAY = {"wind_max": 4.0, "gust_max": 7.0, "temp_min": 8.0, "temp_max": 25.0, "rh_min": 40.0,
         "dry_hours_after": 6, "first_hour": 5, "last_hour": 21, "min_hours": 2}
FROST_C, HEAT_C = 0.0, 30.0

SPRAY_METHOD = {
    "uk": "Орієнтовні загальні пороги: вітер до 4 м/с, пориви до 7 м/с, 8–25 °C, вологість повітря від 40%, без дощу "
          "6 годин після; щонайменше 2 години поспіль між 5:00 і 21:00. Перевірте етикетку препарату.",
    "en": "Rough general thresholds: wind up to 4 m/s, gusts up to 7 m/s, 8–25 °C, air humidity from 40%, no rain for "
          "6 hours after; at least 2 hours in a row between 5:00 and 21:00. Check the product label.",
}
ALERT_METHOD = {
    "uk": "Найнижча температура 0 °C або нижче: можливий заморозок; найвища 30 °C або вище: спека.",
    "en": "Lowest temperature 0 °C or below: frost possible; highest 30 °C or above: heat.",
}
WORDS = {
    "uk": {"none": "немає вдалого часу", "frost": "можливий заморозок", "heat": "спека",
           "weekdays": ("понеділок", "вівторок", "середа", "четвер", "пʼятниця", "субота", "неділя")},
    "en": {"none": "no suitable time", "frost": "frost possible", "heat": "heat",
           "weekdays": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")},
}
SOURCE = Source(provider="Open-Meteo", product_id="open-meteo-forecast", version="best_match", url="https://open-meteo.com/")


class WeatherUnavailable(Exception):
    pass


class OpenMeteoClient:
    def __init__(self, url: str, timeout_s: float, transport: httpx2.AsyncBaseTransport | None = None):
        self.url = url
        self.client = httpx2.AsyncClient(timeout=timeout_s, follow_redirects=False, transport=transport)

    async def forecast(self, lat: float, lon: float, days: int) -> dict[str, Any]:
        params = {"latitude": round(lat, 4), "longitude": round(lon, 4), "daily": ",".join(DAILY),
                  "hourly": ",".join(HOURLY), "wind_speed_unit": "ms", "timezone": "auto", "forecast_days": days}
        return await self.get_json(self.url, params)

    async def get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.client.get(url, params=params)
        except httpx2.HTTPError as e:
            raise WeatherUnavailable(f"forecast service unreachable ({type(e).__name__})") from None
        if response.status_code != 200:
            raise WeatherUnavailable(f"forecast service answered HTTP {response.status_code}")
        try:
            data = response.json()
            # daily or hourly series, whichever was asked for
            if not any(isinstance((data.get(k) or {}).get("time"), list) for k in ("daily", "hourly")):
                raise ValueError
        except ValueError:
            raise WeatherUnavailable("forecast service sent an unreadable answer") from None
        return data

    async def aclose(self) -> None:
        await self.client.aclose()


def spray_window(hours: list[dict[str, Any]]) -> tuple[int, int] | None:
    """Longest run of suitable hours (start hour, end hour exclusive) in one day's local hours, or None."""
    best, run_start = None, None
    for i, h in enumerate(hours + [None]):
        ok = h is not None and _spray_ok(hours, i)
        if ok and run_start is None:
            run_start = i
        if not ok and run_start is not None:
            if i - run_start >= SPRAY["min_hours"] and (best is None or i - run_start > best[1] - best[0]):
                best = (run_start, i)
            run_start = None
    return (hours[best[0]]["hour"], hours[best[1] - 1]["hour"] + 1) if best else None


def _spray_ok(hours: list[dict[str, Any]], i: int) -> bool:
    h = hours[i]
    if not (SPRAY["first_hour"] <= h["hour"] < SPRAY["last_hour"]):
        return False
    values = (h["wind"], h["gusts"], h["temp"], h["rh"])
    if any(v is None for v in values):
        return False
    wind, gusts, temp, rh = values
    after = [x["rain"] for x in hours[i:i + SPRAY["dry_hours_after"] + 1]]
    return (wind <= SPRAY["wind_max"] and gusts <= SPRAY["gust_max"] and SPRAY["temp_min"] <= temp <= SPRAY["temp_max"]
            and rh >= SPRAY["rh_min"] and all(r is not None and r <= 0.1 for r in after))


def facts_from_forecast(data: dict[str, Any], lang: str, retrieved_at: dt.datetime) -> tuple[list[Fact], list[dict[str, Any]]]:
    """The forecast as Facts, plus a per-day summary for the model (weekday words, refs, alerts)."""
    daily, hourly = data["daily"], data.get("hourly", {})
    words = WORDS[lang]
    by_day: dict[str, list[dict[str, Any]]] = {}
    for j, stamp in enumerate(hourly.get("time", [])):
        day, hour = stamp[:10], int(stamp[11:13])
        by_day.setdefault(day, []).append({
            "hour": hour, "temp": _at(hourly, "temperature_2m", j), "rh": _at(hourly, "relative_humidity_2m", j),
            "rain": _at(hourly, "precipitation", j), "wind": _at(hourly, "wind_speed_10m", j),
            "gusts": _at(hourly, "wind_gusts_10m", j)})
    facts, days = [], []
    for i, day_text in enumerate(daily["time"]):
        day = dt.date.fromisoformat(day_text)
        refs: dict[str, str] = {}
        for var, (name, label, unit, aggregation) in _DAILY_FACTS.items():
            value = _at(daily, var, i)
            if value is None:
                continue
            fid = f"wx.{day_text}.{name}"
            facts.append(Fact(id=fid, domain="climate", label_key=label, quantity=Quantity(value=value, unit=unit),
                              provenance=Provenance(kind="weather_data", data_status="live", source=SOURCE,
                                                    temporal=Temporal(start=day, end=day, aggregation=aggregation),
                                                    retrieved_at=retrieved_at)))
            refs[name] = fid
        calc = {"temporal": Temporal(start=day, end=day), "retrieved_at": retrieved_at, "inputs": list(refs.values())}
        window = spray_window(by_day.get(day_text, []))
        fid = f"wx.{day_text}.spray"
        facts.append(Fact(id=fid, domain="climate", label_key="fact.spray_window",
                          category=f"{window[0]:02d}:00–{window[1]:02d}:00" if window else words["none"],
                          provenance=Provenance(kind="deterministic_calc", data_status="live", method=SPRAY_METHOD[lang], **calc)))
        refs["spray_window"] = fid
        low, high = _at(daily, "temperature_2m_min", i), _at(daily, "temperature_2m_max", i)
        alerts = [k for k, hit in (("frost", low is not None and low <= FROST_C),
                                   ("heat", high is not None and high >= HEAT_C)) if hit]
        if alerts:
            fid = f"wx.{day_text}.alert"
            facts.append(Fact(id=fid, domain="climate", label_key="fact.frost_heat",
                              category=", ".join(words[a] for a in alerts),
                              provenance=Provenance(kind="deterministic_calc", data_status="live", method=ALERT_METHOD[lang], **calc)))
            refs["alert"] = fid
        days.append({"date": day_text, "weekday": words["weekdays"][day.weekday()], "refs": refs, "alerts": alerts})
    return facts, days


def _at(block: dict[str, Any], key: str, i: int) -> float | None:
    values = block.get(key) or []
    v = values[i] if i < len(values) else None
    return float(v) if isinstance(v, (int, float)) else None


# ------------------------------------------------------------------ the season so far (since sowing)

SEASON_DAILY = ("temperature_2m_max", "temperature_2m_min", "precipitation_sum", "et0_fao_evapotranspiration")
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
RECENT_DAYS = 7  # the archive (ERA5) lags a few days; the last days come from the forecast service's past days
MIN_COVERAGE = 0.9

# Base temperatures for degree-days (simple average method; maize also caps the day at 30 °C, the "86/50" rule).
# ponytail: one general value per crop; hybrids and local practice differ.
CROPS = {
    "maize": {"base": 10.0, "cap": 30.0, "uk": "кукурудза", "en": "maize"},
    "winter_wheat": {"base": 0.0, "cap": None, "uk": "озима пшениця", "en": "winter wheat"},
    "sunflower": {"base": 8.0, "cap": None, "uk": "соняшник", "en": "sunflower"},
    "soybean": {"base": 10.0, "cap": None, "uk": "соя", "en": "soybean"},
}

# Crop water need, FAO-56 Chapter 6, read in the source (data/knowledge/raw/fao56-ch06.html, 2026-11-11).
# Table 11 stage lengths (initial, development, mid, late; days): the row closest to Ukraine's spring/autumn sowing.
# Table 12 Kc (initial from the crop group, mid, end) for a subhumid climate (RHmin ~45 %, wind ~2 m/s), not adjusted.
#   maize: "Maize (grain) 30 40 50 30, April, Spain (spr, sum.); Calif."; Kc 0.3 (cereals) / 1.20 / 0.60 (footnote
#          11: harvest at high grain moisture; 0.35 if dried in the field).
#   winter wheat: "160 75 75 25, October, Idaho, USA"; Kc 0.4 (frozen soils) / 1.15 / 0.25 (machine harvest).
#   sunflower: "25 35 45 25, April/May, Medit.; California"; Kc 0.35 (oil crops) / 1.0 (footnote 9: lower value for
#          rainfed crops) / 0.35.
#   soybean: "20 30/35 60 25 (140), May, Central USA"; Kc 0.4 (legumes) / 1.15 / 0.50.
# ponytail: one table row per crop and no climate adjustment (FAO-56 eq. 62); local crop coefficients would be better.
FAO_KC = {
    "maize": {"stages": (30, 40, 50, 30), "kc": (0.3, 1.2, 0.6), "row": "кукурудза на зерно, посів у квітні", "row_en": "grain maize sown in April"},
    "winter_wheat": {"stages": (160, 75, 75, 25), "kc": (0.4, 1.15, 0.25), "row": "озима пшениця, посів у жовтні", "row_en": "winter wheat sown in October"},
    "sunflower": {"stages": (25, 35, 45, 25), "kc": (0.35, 1.0, 0.35), "row": "соняшник без поливу", "row_en": "rainfed sunflower"},
    "soybean": {"stages": (20, 35, 60, 25), "kc": (0.4, 1.15, 0.5), "row": "соя, посів у травні", "row_en": "soybean sown in May"},
}


def crop_kc(day: int, crop: str) -> float | None:
    """FAO-56 crop coefficient on day `day` after sowing (0 = sowing day): flat, rising, flat, falling (eq. 66);
    None once the table's season is over."""
    ini, dev, mid, late = FAO_KC[crop]["stages"]
    kc_ini, kc_mid, kc_end = FAO_KC[crop]["kc"]
    i = day + 1
    if i <= ini:
        return kc_ini
    if i <= ini + dev:
        return kc_ini + (i - ini) / dev * (kc_mid - kc_ini)
    if i <= ini + dev + mid:
        return kc_mid
    if i <= ini + dev + mid + late:
        return kc_mid + (i - ini - dev - mid) / late * (kc_end - kc_mid)
    return None
SEASON_SOURCE = Source(provider="Open-Meteo", product_id="open-meteo-archive (ERA5) + recent days", version="unverified",
                       url="https://open-meteo.com/")


async def season_history(client: OpenMeteoClient, lat: float, lon: float, start: dt.date, end: dt.date,
                         archive_url: str = ARCHIVE_URL) -> dict[str, dict[str, float | None]]:
    """Daily values from `start` to `end`: the archive first, then the recent days the archive does not have yet."""
    base = {"latitude": round(lat, 4), "longitude": round(lon, 4), "daily": ",".join(SEASON_DAILY), "timezone": "auto"}
    days: dict[str, dict[str, float | None]] = {}
    archive = await client.get_json(archive_url, {**base, "start_date": start.isoformat(), "end_date": end.isoformat()})
    _merge(days, archive)
    recent = await client.get_json(client.url, {**base, "past_days": RECENT_DAYS, "forecast_days": 1})
    _merge(days, recent, only_missing=True)
    return {d: v for d, v in days.items() if start.isoformat() <= d <= end.isoformat()}


def _merge(days: dict[str, dict[str, float | None]], data: dict[str, Any], only_missing: bool = False) -> None:
    daily = data.get("daily") or {}
    for i, d in enumerate(daily.get("time") or []):
        row = {k: _at(daily, k, i) for k in SEASON_DAILY}
        if only_missing and d in days and all(v is not None for v in days[d].values()):
            continue
        days[d] = row


def season_facts(history: dict[str, dict[str, float | None]], start: dt.date, end: dt.date, crop: str | None,
                 lang: str, retrieved_at: dt.datetime) -> list[Fact]:
    """Rain, evaporation, rain minus evaporation and (with a crop) degree-days since `start`, as Facts.
    Raises WeatherUnavailable when too few days have data to be honest about the sum."""
    expected = (end - start).days + 1
    dated = {d: v for d, v in history.items() if all(x is not None for x in v.values())}
    complete = list(dated.values())
    if expected <= 0 or len(complete) < MIN_COVERAGE * expected:
        raise WeatherUnavailable(f"weather history covers {len(complete)} of {expected} days")
    rain = round(sum(v["precipitation_sum"] for v in complete), 1)
    et0 = round(sum(v["et0_fao_evapotranspiration"] for v in complete), 1)
    period = Temporal(start=start, end=end, aggregation="sum")
    n = f"{len(complete)}/{expected}"
    uk = lang == "uk"
    measured = {"kind": "weather_data", "data_status": "live", "source": SEASON_SOURCE, "temporal": period,
                "retrieved_at": retrieved_at}
    facts = [
        Fact(id="season.rain", domain="water", label_key="fact.precipitation_total", quantity=Quantity(value=rain, unit="mm"),
             provenance=Provenance(**measured, method=f"daily sums, {n} days")),
        Fact(id="season.et0", domain="water", label_key="fact.reference_et0", quantity=Quantity(value=et0, unit="mm"),
             provenance=Provenance(**measured, method=f"FAO-56 Penman-Monteith reference evapotranspiration, daily sums, {n} days")),
        Fact(id="season.water_balance", domain="water", label_key="fact.water_balance",
             quantity=Quantity(value=round(rain - et0, 1), unit="mm"),
             provenance=Provenance(kind="deterministic_calc", data_status="live", temporal=period, retrieved_at=retrieved_at,
                                   inputs=["season.rain", "season.et0"],
                                   method=("Опади мінус випаровування з поверхні трави за FAO-56 (без урахування культури "
                                           "й запасу води в ґрунті). Мінус означає, що випарувалося більше, ніж випало.") if uk else
                                          ("Rain minus reference (grass) evaporation, FAO-56, without crop or soil water. "
                                           "Negative means more evaporated than fell."))),
    ]
    if crop:
        c = CROPS[crop]
        gdd = 0.0
        for v in complete:
            high, low = v["temperature_2m_max"], v["temperature_2m_min"]
            if c["cap"] is not None:
                high, low = min(high, c["cap"]), max(low, c["base"])
            gdd += max(0.0, (high + low) / 2 - c["base"])
        rule = (f"Сума (найвища + найнижча)/2 мінус {c['base']:g} °C за кожен день від сівби для культури «{c['uk']}»"
                + (f"; день обмежено {c['cap']:g} °C" if c["cap"] is not None else "") + ". Загальне правило, гібриди різняться."
                if uk else
                f"Sum of (high + low)/2 minus {c['base']:g} °C for each day since sowing, {c['en']}"
                + (f"; days capped at {c['cap']:g} °C" if c["cap"] is not None else "") + ". A general rule; hybrids differ.")
        facts.append(Fact(id="season.gdd", domain="climate", label_key="fact.growing_degree_days",
                          quantity=Quantity(value=round(gdd), unit="Cel.d"),
                          provenance=Provenance(kind="deterministic_calc", data_status="live", temporal=period,
                                                retrieved_at=retrieved_at, method=rule)))
        facts += _crop_water(dated, start, crop, uk, period, retrieved_at)
    return facts


def _crop_water(dated: dict[str, dict[str, float | None]], start: dt.date, crop: str, uk: bool, period: Temporal,
                retrieved_at: dt.datetime) -> list[Fact]:
    """Water a well-watered crop would have used since sowing (grass evaporation x FAO-56 crop coefficient), and
    rain minus that, over the days inside the table's season."""
    need = rain = 0.0
    for d, v in dated.items():
        kc = crop_kc((dt.date.fromisoformat(d) - start).days, crop)
        if kc is not None:
            need += kc * v["et0_fao_evapotranspiration"]
            rain += v["precipitation_sum"]
    f = FAO_KC[crop]
    ini, dev, mid, late = f["stages"]
    kc_ini, kc_mid, kc_end = f["kc"]
    season = ini + dev + mid + late
    past = max(dt.date.fromisoformat(d) for d in dated) >= start + dt.timedelta(days=season)
    if past:  # the crop's own dates, so its values are not read against the whole period's rain
        period = Temporal(start=start, end=start + dt.timedelta(days=season - 1), aggregation="sum")
    num = lambda x: f"{x:g}".replace(".", ",") if uk else f"{x:g}"  # noqa: E731
    method = (f"Випаровування з трави × коефіцієнт культури з таблиць 11 і 12 FAO-56 ({f['row']}: етапи "
              f"{ini}/{dev}/{mid}/{late} днів, коефіцієнт {num(kc_ini)} → {num(kc_mid)} → {num(kc_end)}). Стільки "
              "води взяли б рослини без її нестачі; гібрид і густота це змінюють."
              + (f" Враховано лише {season} днів сезону за таблицею." if past else "")) if uk else (
              f"Grass evaporation x crop coefficient from FAO-56 Tables 11 and 12 ({f['row_en']}: stages "
              f"{ini}/{dev}/{mid}/{late} days, coefficient {kc_ini:g} -> {kc_mid:g} -> {kc_end:g}). What plants with "
              "enough water would use; hybrid and density change it."
              + (f" Only the table's {season}-day season is counted." if past else ""))
    calc = {"kind": "deterministic_calc", "data_status": "live", "temporal": period, "retrieved_at": retrieved_at}
    return [
        Fact(id="season.crop_water_use", domain="water", label_key="fact.crop_water_use",
             quantity=Quantity(value=round(need, 1), unit="mm"),
             provenance=Provenance(**calc, inputs=["season.et0"], method=method)),
        Fact(id="season.crop_rain", domain="water", label_key="fact.precipitation_total",
             quantity=Quantity(value=round(rain, 1), unit="mm"),
             provenance=Provenance(kind="weather_data", data_status="live", source=SEASON_SOURCE, temporal=period,
                                   retrieved_at=retrieved_at, method="daily sums over the crop's season")),
        Fact(id="season.crop_water_balance", domain="water", label_key="fact.crop_water_balance",
             quantity=Quantity(value=round(rain - need, 1), unit="mm"),
             provenance=Provenance(**calc, inputs=["season.crop_rain", "season.crop_water_use"],
                                   method=("Опади мінус потреба культури у воді за той самий час (без запасу води в "
                                           "ґрунті). Мінус означає, що дощу не вистачило і рослини брали воду з ґрунту "
                                           "або недоотримали її.") if uk else
                                          ("Rain minus the crop's water need over the same days (soil water not "
                                           "counted). Negative means rain fell short: plants drew on the soil or went "
                                           "without."))),
    ]


# ------------------------------------------------------------------ is the soil warm enough to sow?

# Thresholds read in the source itself (2026-11-10): "Проведення весняно-польових робіт в АПК Черкаської області в
# умовах 2024 року (науково-практичні рекомендації)", Черкаська державна СГДС ННЦ «Інститут землеробства НААН».
# Sunflower p. 26: «Сівбу проводять за стійкого прогрівання шару грунту 0-10 см до 8…10°C».
# Soybean p. 22: «Сою починають сіяти за температури ґрунту 10-12°C (орієнтовно третя декада квітня – перша декада
# травня)». Regional (Forest-Steppe); no maize threshold in that document, so maize is not offered.
SOW_THRESHOLDS = {"sunflower": (8.0, 10.0, 26), "soybean": (10.0, 12.0, 22)}
STEADY_DAYS = 3  # "стійке прогрівання" read as the daily mean at or above the threshold three days in a row
SOW_SOURCE = Source(provider="Черкаська ДСГДС ННЦ «Інститут землеробства НААН»", product_id="cherkasy-naas-spring-2024",
                    version="2024")


async def soil_temperature(client: OpenMeteoClient, lat: float, lon: float) -> tuple[dict[str, list[float]], dt.date]:
    """Hourly soil temperature at 6 cm (the nearest model layer to 0-10 cm), the last 3 days and the next 7, by the
    field's local day; and the field's local date today."""
    data = await client.get_json(client.url, {"latitude": round(lat, 4), "longitude": round(lon, 4),
                                              "hourly": "soil_temperature_6cm", "past_days": STEADY_DAYS,
                                              "forecast_days": 7, "timezone": "auto"})
    hourly = data.get("hourly") or {}
    days: dict[str, list[float]] = {}
    for i, stamp in enumerate(hourly.get("time") or []):
        v = _at(hourly, "soil_temperature_6cm", i)
        if v is not None:
            days.setdefault(stamp[:10], []).append(v)
    offset = dt.timedelta(seconds=int(data.get("utc_offset_seconds") or 0))
    today = (dt.datetime.now(dt.UTC) + offset).date()
    return {d: v for d, v in days.items() if len(v) >= 20}, today  # whole days only


def sowing_facts(days: dict[str, list[float]], crop: str, today: dt.date, lang: str,
                 retrieved_at: dt.datetime) -> list[Fact]:
    low, high, page = SOW_THRESHOLDS[crop]
    means = {dt.date.fromisoformat(d): sum(v) / len(v) for d, v in sorted(days.items())}
    if today not in means:
        raise WeatherUnavailable("no soil temperature for today")
    warm = [d for d, m in means.items() if m >= low]
    steady = next((d for d in sorted(means) if all(d - dt.timedelta(days=k) in warm for k in range(STEADY_DAYS))), None)
    last = max(means)
    uk = lang == "uk"
    name = CROPS[crop][lang]
    if steady is not None and steady <= today:
        verdict = "так, ґрунт стійко прогрівся" if uk else "yes, the soil is steadily warm"
    elif steady is not None:
        verdict = (f"ще ні; за прогнозом приблизно з {steady:%d.%m}" if uk else f"not yet; by the forecast from about {steady:%d %b}")
    else:
        verdict = (f"ще ні, і за прогнозом до {last:%d.%m} теж ні" if uk else f"not yet, and not in the forecast up to {last:%d %b}")
    now = Fact(id="sowing.soil_now", domain="soil", label_key="fact.soil_temperature_6cm",
               quantity=Quantity(value=round(means[today], 1), unit="Cel"),
               provenance=Provenance(kind="weather_data", data_status="live", source=SOURCE, retrieved_at=retrieved_at,
                                     temporal=Temporal(start=today, end=today, aggregation="daily mean at 6 cm")))
    rule = Fact(id="sowing.threshold", domain="soil", label_key="fact.sowing_soil_threshold",
                quantity=Quantity(value=low, unit="Cel", uncertainty={"kind": "range", "low": low, "high": high}),
                provenance=Provenance(kind="reference", data_status="live", source=SOW_SOURCE, retrieved_at=retrieved_at,
                                      method=(f"{name}: с. {page}; рекомендації для Черкащини (Лісостеп), шар ґрунту 0–10 см"
                                              if uk else f"{name}: p. {page}; recommendations for Cherkasy region (Forest-Steppe), soil 0–10 cm")))
    ready = Fact(id="sowing.ready", domain="soil", label_key="fact.sowing_soil_ready", category=verdict,
                 provenance=Provenance(kind="deterministic_calc", data_status="live", retrieved_at=retrieved_at,
                                       inputs=["sowing.soil_now", "sowing.threshold"],
                                       temporal=Temporal(start=min(means), end=last),
                                       method=(f"Середня за добу температура ґрунту на 6 см (найближчий шар прогнозу до 0–10 см) не нижче "
                                               f"{low:g} °C {STEADY_DAYS} дні поспіль; минулі 3 дні й прогноз на 7 днів." if uk else
                                               f"Daily mean soil temperature at 6 cm (the forecast layer nearest to 0–10 cm) at or above "
                                               f"{low:g} °C for {STEADY_DAYS} days in a row; the last 3 days and a 7-day forecast.")))
    return [now, rule, ready]
