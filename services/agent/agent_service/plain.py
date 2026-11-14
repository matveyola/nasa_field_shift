"""Plain-language display names for what the agent shows a farmer.

The assistant is read mostly by farmers without IT or remote-sensing training, so answers name things in
everyday words: "Вологість ґрунту біля коренів (супутник NASA SMAP)", not "root zone soil moisture
(SPL4SMGP v008)". These names are for display only. Ids, product ids and versions stay unchanged in every
contract and in each fact's provenance (the widget's "data used" panel still shows the exact product and
version), and no grounding check depends on them.

Unknown keys fall back to readable words; unknown products to their own id. The fact and layer names are the widget's
catalogue, lowercased (frontend/src/widget/i18n/strings.ts `labels`).
"""

import datetime as dt

NAMES: dict[str, dict[str, str]] = {
    "uk": {
        "fact.air_temperature_max_mean": "середня найвища денна температура",
        "fact.forecast_min_root_zone_reserve": "мінімальний запас води біля коренів за прогнозом",
        "fact.forecast_precipitation_total": "сума опадів за прогнозом",
        "fact.forecast_root_zone_reserve": "запас води біля коренів за прогнозом",
        "fact.growing_degree_days": "сума ефективних температур",
        "fact.historical_min_root_zone_reserve": "мінімальний запас води біля коренів за розрахунком",
        "fact.historical_precipitation_total": "сума опадів у сценарії",
        "fact.historical_root_zone_reserve": "запас води біля коренів за розрахунком",
        "fact.hydrologic_soil_group": "як ґрунт вбирає воду",
        "fact.legume_n_credit": "азот, що лишили бобові (горох, соя тощо)",
        "fact.ndvi_clear_coverage": "частка поля з придатними супутниковими даними",
        "fact.ndvi_latest": "останній індекс рослинності NDVI",
        "fact.ndvi_low_area_share": "частка спостереженої площі з нижчим NDVI",
        "fact.ndvi_peak": "найкращий стан посівів за сезон",
        "fact.precipitation_total": "сума опадів",
        "fact.reference_et0": "скільки води випаровується",
        "fact.root_zone_soil_moisture": "вологість ґрунту біля коренів",
        "fact.root_zone_soil_moisture_downscaled": "вологість ґрунту біля коренів (детально)",
        "fact.rotation_water_risk": "ризик нестачі води",
        "fact.rotation_yield_loss_water": "втрата врожаю через нестачу води",
        "fact.slope_mean": "середній ухил",
        "fact.elevation_min": "найнижча точка поля",
        "fact.elevation_max": "найвища точка поля",
        "fact.surface_soil_moisture": "вологість верхнього шару ґрунту (0–7 см, оцінка погодної моделі)",
        "fact.farmer_amount": "сума з ваших слів",
        "fact.farmer_amount_per_hectare": "сума на гектар з ваших слів",
        "fact.farmer_area": "площа з ваших слів",
        "fact.budget_per_hectare": "сума на гектар (розрахунок з ваших чисел)",
        "fact.budget_total_for_area": "сума на всю площу (розрахунок з ваших чисел)",
        "fact.budget_difference": "різниця (розрахунок з ваших чисел)",
        "fact.soil_clay": "вміст глини",
        "fact.soil_ph": "кислотність ґрунту (pH)",
        "fact.water_stress_days": "дні з водним стресом",
        "fact.water_stress_threshold": "поріг водного стресу",
        "fact.forecast_temp_max": "найвища температура вдень",
        "fact.forecast_temp_min": "найнижча температура вночі",
        "fact.forecast_rain": "опади",
        "fact.forecast_rain_chance": "ймовірність опадів",
        "fact.forecast_wind_max": "найсильніший вітер",
        "fact.forecast_wind_gust": "пориви вітру",
        "fact.spray_window": "коли можна обприскувати",
        "fact.water_balance": "опади мінус випаровування",
        "fact.crop_water_use": "потреба культури у воді",
        "fact.crop_water_balance": "опади мінус потреба культури",
        "fact.soil_temperature_6cm": "температура ґрунту на глибині 6 см",
        "fact.sowing_soil_threshold": "поріг прогрівання ґрунту для сівби",
        "fact.sowing_soil_ready": "чи прогрівся ґрунт для сівби",
        "fact.frost_heat": "заморозок чи спека",
        "layer.hls_ndvi": "стан посівів (супутник)",
        "layer.ndvi_variability": "неоднорідність посівів за NDVI",
        "layer.slope": "ухил",
        "layer.smap_rootzone": "вологість ґрунту (супутник)",
        "layer.soil_moisture_downscaled": "вологість ґрунту детально (оцінка)",
    },
    "en": {
        "fact.air_temperature_max_mean": "average daytime high temperature",
        "fact.forecast_min_root_zone_reserve": "forecast minimum root-zone water reserve",
        "fact.forecast_precipitation_total": "forecast total rainfall",
        "fact.forecast_root_zone_reserve": "forecast root-zone water reserve",
        "fact.growing_degree_days": "growing degree days",
        "fact.historical_min_root_zone_reserve": "calculated minimum root-zone water reserve",
        "fact.historical_precipitation_total": "scenario total rainfall",
        "fact.historical_root_zone_reserve": "calculated root-zone water reserve",
        "fact.hydrologic_soil_group": "how well the soil soaks up water",
        "fact.legume_n_credit": "nitrogen left by peas, beans or other legumes",
        "fact.ndvi_clear_coverage": "share of the field covered by usable satellite data",
        "fact.ndvi_latest": "latest vegetation index (NDVI)",
        "fact.ndvi_low_area_share": "share of the observed area with lower NDVI",
        "fact.ndvi_peak": "best crop growth this season",
        "fact.precipitation_total": "total rainfall",
        "fact.reference_et0": "water lost to evaporation",
        "fact.root_zone_soil_moisture": "soil moisture around the roots",
        "fact.root_zone_soil_moisture_downscaled": "soil moisture around the roots (detailed)",
        "fact.rotation_water_risk": "risk of water shortage",
        "fact.rotation_yield_loss_water": "harvest lost to water shortage",
        "fact.slope_mean": "average slope",
        "fact.elevation_min": "lowest point of the field",
        "fact.elevation_max": "highest point of the field",
        "fact.surface_soil_moisture": "topsoil moisture (0–7 cm, weather-model estimate)",
        "fact.farmer_amount": "amount you gave",
        "fact.farmer_amount_per_hectare": "amount per hectare you gave",
        "fact.farmer_area": "area you gave",
        "fact.budget_per_hectare": "amount per hectare (calculated from your figures)",
        "fact.budget_total_for_area": "amount for the whole area (calculated from your figures)",
        "fact.budget_difference": "difference (calculated from your figures)",
        "fact.soil_clay": "clay content",
        "fact.soil_ph": "soil acidity (pH)",
        "fact.water_stress_days": "days with water stress",
        "fact.water_stress_threshold": "water-stress threshold",
        "fact.forecast_temp_max": "daytime high",
        "fact.forecast_temp_min": "night-time low",
        "fact.forecast_rain": "rain",
        "fact.forecast_rain_chance": "chance of rain",
        "fact.forecast_wind_max": "strongest wind",
        "fact.forecast_wind_gust": "wind gusts",
        "fact.spray_window": "when spraying is suitable",
        "fact.water_balance": "rain minus evaporation",
        "fact.crop_water_use": "crop water need",
        "fact.crop_water_balance": "rain minus crop water need",
        "fact.soil_temperature_6cm": "soil temperature at 6 cm",
        "fact.sowing_soil_threshold": "soil warmth needed to sow",
        "fact.sowing_soil_ready": "is the soil warm enough to sow",
        "fact.frost_heat": "frost or heat",
        "layer.hls_ndvi": "crop condition (satellite)",
        "layer.ndvi_variability": "vegetation variability by NDVI",
        "layer.slope": "slope",
        "layer.smap_rootzone": "soil moisture (satellite)",
        "layer.soil_moisture_downscaled": "soil moisture, detailed (estimate)",
    },
}

# product id prefix -> plain source name (first match wins)
SOURCES: tuple[tuple[str, dict[str, str]], ...] = (
    ("SPL4SMGP", {"uk": "супутник NASA SMAP", "en": "NASA SMAP satellite"}),
    ("GPM_", {"uk": "супутники NASA GPM", "en": "NASA GPM satellites"}),
    ("HLS", {"uk": "супутникові знімки NASA (Landsat і Sentinel)", "en": "NASA satellite images (Landsat and Sentinel)"}),
    ("NASADEM", {"uk": "карта рельєфу NASA", "en": "NASA terrain map"}),
    ("Copernicus DEM", {"uk": "карта рельєфу Copernicus", "en": "Copernicus terrain map"}),
    ("ERA5-Land", {"uk": "оцінка погодної моделі ERA5-Land, не супутник і не заміри на полі",
                   "en": "ERA5-Land weather-model estimate, not a satellite or field measurement"}),
    ("SoilGrids", {"uk": "світова карта ґрунтів SoilGrids", "en": "SoilGrids world soil map"}),
    ("HYSOGs", {"uk": "світова карта ґрунтів HYSOGs", "en": "HYSOGs world soil map"}),
    ("T2M", {"uk": "погодні дані NASA POWER", "en": "NASA POWER weather data"}),
    ("cherkasy-naas", {"uk": "рекомендації Черкаської дослідної станції НААН (2024)",
                       "en": "recommendations of the Cherkasy research station of NAAS (2024)"}),
    ("open-meteo-archive", {"uk": "архів погоди Open-Meteo (ERA5)", "en": "Open-Meteo weather archive (ERA5)"}),
    ("open-meteo", {"uk": "прогноз погоди Open-Meteo", "en": "Open-Meteo weather forecast"}),
)

UNITS = {
    "uk": {"1": "", "[pH]": "", "m3/m3": " м³/м³", "Cel": " °C", "Cel.d": " °C·днів", "deg": "°", "%": "%",
           "mm": " мм", "g/kg": " г/кг", "kg/ha": " кг/га", "d": " днів", "m/s": " м/с", "m": " м",
           "ha": " га", "UAH": " грн", "UAH/ha": " грн/га", "USD": " дол. США", "USD/ha": " дол. США/га"},
    "en": {"1": "", "[pH]": "", "m3/m3": " m³/m³", "Cel": " °C", "Cel.d": " °C·d", "deg": "°", "%": "%",
           "mm": " mm", "g/kg": " g/kg", "kg/ha": " kg/ha", "d": " days", "m/s": " m/s", "m": " m",
           "ha": " ha", "UAH": " UAH", "UAH/ha": " UAH/ha", "USD": " USD", "USD/ha": " USD/ha"},
}

CROPS = {
    "uk": {"maize": "кукурудза", "sorghum": "сорго", "soybean": "соя", "sunflower": "соняшник", "winter_pea": "озимий горох",
           "winter_rye_vetch": "озиме жито з викою (покривна культура)", "winter_wheat": "озима пшениця"},
    "en": {"maize": "maize", "sorghum": "sorghum", "soybean": "soybean", "sunflower": "sunflower", "winter_pea": "winter pea",
           "winter_rye_vetch": "winter rye with vetch (cover crop)", "winter_wheat": "winter wheat"},
}
ROTATIONS = {
    "uk": {"rot_adaptive": "Адаптивна сівозміна", "rot_conventional": "Звичайна сівозміна"},
    "en": {"rot_adaptive": "Adaptive rotation", "rot_conventional": "Conventional rotation"},
}

_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def name(key: str, lang: str) -> str:
    """A fact or layer label key as plain words: 'fact.soil_ph' -> 'кислотність ґрунту (pH)'."""
    return NAMES[lang].get(key) or key.split(".", 1)[-1].replace("_", " ")


def source(product_id: str | None, lang: str) -> str:
    if not product_id:
        return ""
    return next((names[lang] for prefix, names in SOURCES if product_id.startswith(prefix)), product_id)


def unit(code: str, lang: str, value: float | None = None) -> str:
    if code == "d" and value is not None:  # days agree with the number: 1 день, 2 дні, 5 днів; 1 day, 2 days
        return " " + _days(value, lang)
    return UNITS[lang].get(code, " " + code)


def _days(value: float, lang: str) -> str:
    if value != int(value):
        return "дня" if lang == "uk" else "days"
    n = abs(int(value))
    if lang == "en":
        return "day" if n == 1 else "days"
    if n % 10 == 1 and n % 100 != 11:
        return "день"
    return "дні" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "днів"


def period(start: dt.date, end: dt.date, lang: str) -> str:
    """'01.05–31.07.2026' / '1 May – 31 Jul 2026' (years repeated only when they differ); one day: '11.11.2026'."""
    if start == end:
        return f"{start:%d.%m.%Y}" if lang == "uk" else f"{start.day} {_MONTHS_EN[start.month - 1]} {start.year}"
    if lang == "uk":
        if start.year == end.year:
            return f"{start:%d.%m}–{end:%d.%m.%Y}"
        return f"{start:%d.%m.%Y}–{end:%d.%m.%Y}"
    a = f"{start.day} {_MONTHS_EN[start.month - 1]}"
    b = f"{end.day} {_MONTHS_EN[end.month - 1]} {end.year}"
    return f"{a} – {b}" if start.year == end.year else f"{a} {start.year} – {b}"


def crop(crop_id: str, lang: str) -> str:
    return CROPS[lang].get(crop_id) or crop_id.replace("_", " ")


def rotation(rotation_id: str, lang: str) -> str:
    return ROTATIONS[lang].get(rotation_id) or rotation_id.removeprefix("rot_").replace("_", " ").capitalize()
