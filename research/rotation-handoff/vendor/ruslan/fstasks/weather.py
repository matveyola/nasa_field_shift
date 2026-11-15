"""Daily weather: data model, synthetic Iowa climate, NASA POWER and Open-Meteo clients.

No network was available while building this package. All results in outputs/ use the
SYNTHETIC generator below (source tag "synthetic-iowa-v1"). The POWER and Open-Meteo
clients are ready to run where those hosts are reachable; their parsers are tested on
response samples with the documented structure.
"""

from __future__ import annotations

import json
import math
import random
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from fstasks.meteo import eto_daily, extraterrestrial_radiation, rs_hargreaves

DEMO_LAT, DEMO_LON, DEMO_ELEV_M = 41.704, -93.413, 285.0  # Bondurant, Iowa demo fields
DEMO_TZ = "America/Chicago"
SYNTHETIC_SOURCE = "synthetic-iowa-v1"


@dataclass(frozen=True, slots=True)
class WeatherDay:
    date: date
    tmax: float
    tmin: float
    rain_mm: float
    et0_mm: float
    rhmax: float | None = None
    rhmin: float | None = None
    u2: float | None = None
    rs_mj: float | None = None
    source: str = ""
    gap: bool = False  # True when any input was missing and filled
    rhmean: float | None = None  # daily mean RH, % (NASA POWER RH2M)


# --------------------------------------------------------------------------- synthetic climate
# Approximate 1991-2020 monthly normals for Des Moines, Iowa (from memory; check against NOAA).
_PRCP = (25, 30, 55, 90, 120, 125, 110, 105, 80, 65, 50, 35)
_WET_DAYS = (8, 8, 10, 11, 13, 11, 9, 9, 8, 9, 8, 8)
_TMAX = (-2, 1, 9, 17, 23, 28, 30, 29, 25, 18, 9, 1)
_TMIN = (-11, -8, -2, 4, 11, 17, 19, 18, 13, 6, -1, -8)
_U2 = (3.4, 3.5, 3.8, 3.9, 3.4, 3.0, 2.6, 2.5, 2.8, 3.1, 3.4, 3.3)
_RHMIN = (62, 60, 55, 50, 52, 54, 56, 56, 52, 50, 57, 63)
_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _monthly_interp(values: tuple, d: date) -> float:
    """Linear interpolation between mid-month values."""
    mid = d.replace(day=15)
    if d >= mid:
        m0, m1 = d.month - 1, d.month % 12
        nxt = date(d.year + (d.month == 12), m1 + 1, 15)
        frac = (d - mid).days / (nxt - mid).days
    else:
        m1, m0 = d.month - 1, (d.month - 2) % 12
        prev = date(d.year - (d.month == 1), m0 + 1, 15)
        frac = (d - prev).days / (mid - prev).days
    return values[m0] + (values[m1] - values[m0]) * frac


def synthetic_year(year: int, seed: int = 7, lat: float = DEMO_LAT, elev: float = DEMO_ELEV_M) -> list[WeatherDay]:
    """One calendar year of plausible central-Iowa weather (not observations)."""
    rng = random.Random(f"{seed}-{year}")
    month_rain_factor = [math.exp(rng.gauss(-0.06, 0.35)) for _ in range(12)]
    season_factor = math.exp(rng.gauss(-0.03, 0.25))  # drought / wet year signal for Apr-Sep
    out: list[WeatherDay] = []
    wet_prev, t_anom = False, 0.0
    d = date(year, 1, 1)
    while d.year == year:
        m = d.month - 1
        factor = month_rain_factor[m] * (season_factor if 4 <= d.month <= 9 else 1.0)
        p = _WET_DAYS[m] / _DAYS_IN_MONTH[m]
        p11 = min(0.65, p + 0.25)
        p01 = p * (1 - p11) / (1 - p)
        wet = rng.random() < (p11 if wet_prev else p01)
        mean_amt = _PRCP[m] / _WET_DAYS[m] * factor
        rain = rng.gammavariate(0.7, mean_amt / 0.7) if wet else 0.0
        # Hot when dry: monthly anomaly is anti-correlated with the rain factor.
        t_anom = 0.7 * t_anom + rng.gauss(0, 2.2)
        climate_shift = -2.0 * math.log(factor)
        tmax = _monthly_interp(_TMAX, d) + t_anom + climate_shift - (2.5 if wet else 0.0)
        tmin = _monthly_interp(_TMIN, d) + 0.8 * t_anom + 0.5 * climate_shift + (1.0 if wet else 0.0)
        if tmin > tmax - 3:
            tmin = tmax - 3
        rhmin = min(95.0, _monthly_interp(_RHMIN, d) + (18 if wet else 0) - 1.5 * max(climate_shift, 0))
        rhmax = min(100.0, max(rhmin + 20, 88 + rng.gauss(0, 4)))
        u2 = max(0.8, _monthly_interp(_U2, d) * math.exp(rng.gauss(0, 0.3)))
        doy = d.timetuple().tm_yday
        rs = rs_hargreaves(tmax, tmin, lat, doy)
        et0 = eto_daily(tmax, tmin, rs, u2, lat, doy, elev, rhmax=rhmax, rhmin=rhmin)
        out.append(
            WeatherDay(d, round(tmax, 1), round(tmin, 1), round(rain, 1), round(et0, 2),
                       round(rhmax), round(rhmin), round(u2, 2), round(rs, 2), SYNTHETIC_SOURCE)
        )
        wet_prev = wet
        d += timedelta(days=1)
    return out


def synthetic_series(first_year: int, last_year: int, seed: int = 7) -> dict[date, WeatherDay]:
    series: dict[date, WeatherDay] = {}
    for y in range(first_year, last_year + 1):
        for w in synthetic_year(y, seed):
            series[w.date] = w
    return series


_NORMALS: Callable[[date], tuple[float, float]] | None = None


def set_normals(fn: Callable[[date], tuple[float, float]] | None) -> None:
    """Use the field's own climate normals (e.g. NASA POWER 1991-2020) instead of the synthetic
    ones. Call fstasks.crops.reset_calibration() afterwards: stage and sowing calibrations depend
    on the normals."""
    global _NORMALS
    _NORMALS = fn


def normal_temps(d: date) -> tuple[float, float]:
    """Climatological (Tmax, Tmin) for a date. Used to calibrate thermal-time crop stages so that
    FAO-56 stage lengths in days are reproduced under normal weather."""
    if _NORMALS is not None:
        return _NORMALS(d)
    return _monthly_interp(_TMAX, d), _monthly_interp(_TMIN, d)


def normals_from(weather: dict[date, WeatherDay], first_year: int = 1991, last_year: int = 2020,
                 half_window: int = 15) -> Callable[[date], tuple[float, float]]:
    """Day-of-year mean Tmax/Tmin over the given years, smoothed with a +/-15-day window."""
    sums = [[0.0, 0.0, 0] for _ in range(366)]
    for d, w in weather.items():
        if first_year <= d.year <= last_year:
            i = min(d.timetuple().tm_yday, 365) - 1
            sums[i][0] += w.tmax
            sums[i][1] += w.tmin
            sums[i][2] += 1
    if sum(s[2] for s in sums) < 365 * 5:
        raise ValueError("normals need at least five complete years")
    table = []
    for i in range(365):
        acc = [sums[(i + k) % 365] for k in range(-half_window, half_window + 1)]
        n = sum(a[2] for a in acc)
        table.append((sum(a[0] for a in acc) / n, sum(a[1] for a in acc) / n))

    def fn(d: date) -> tuple[float, float]:
        return table[min(d.timetuple().tm_yday, 365) - 1]

    return fn


@dataclass(frozen=True, slots=True)
class DryDayContrast:
    """How a dry day differs from a rain day (>= 1 mm) in the same months: used when the drought
    scenario turns a rain day into a dry one."""

    tmax: float = 2.5
    tmin: float = -1.0
    rh: float = -18.0  # applied to RHmin (synthetic data) or RHmean (observed data)
    rs_ratio: float | None = None  # dry/wet radiation ratio; None = recompute with Hargreaves
    source: str = "synthetic generator wet/dry contrast (ASSUMPTION)"


def dry_day_contrast(weather: dict[date, WeatherDay], months: tuple[int, ...] = (5, 6, 7, 8)) -> DryDayContrast:
    """Estimate the dry-minus-wet contrast from the record itself (days with < 0.5 mm vs >= 1 mm)."""
    wet = [w for d, w in weather.items() if d.month in months and w.rain_mm >= 1.0]
    dry = [w for d, w in weather.items() if d.month in months and w.rain_mm < 0.5]
    if len(wet) < 100 or len(dry) < 100 or any(w.rhmean is None or w.rs_mj is None for w in wet + dry):
        raise ValueError("not enough complete days to estimate the dry-day contrast")

    def avg(rows, f):
        return sum(f(w) for w in rows) / len(rows)

    return DryDayContrast(
        round(avg(dry, lambda w: w.tmax) - avg(wet, lambda w: w.tmax), 2),
        round(avg(dry, lambda w: w.tmin) - avg(wet, lambda w: w.tmin), 2),
        round(avg(dry, lambda w: w.rhmean) - avg(wet, lambda w: w.rhmean), 2),
        round(avg(dry, lambda w: w.rs_mj) / avg(wet, lambda w: w.rs_mj), 3),
        f"field record, months {months}: {len(dry)} dry vs {len(wet)} rain days",
    )


def drop_rain_days(
    day: WeatherDay,
    fraction: float,
    months: tuple[int, ...] = (5, 6, 7, 8),
    seed: int = 11,
    lat: float = DEMO_LAT,
    elev: float = DEMO_ELEV_M,
    contrast: DryDayContrast | None = None,
) -> WeatherDay:
    """Drought scenario: remove a fixed random share of rain days (>= 1 mm) in the given months.

    The choice depends only on the date and seed, so every rotation sees the same weather.
    A removed rain day becomes a typical dry day: Tmax +2.5 °C, Tmin -1 °C, RHmin -18 points
    (the wet/dry contrast of the synthetic generator; an ASSUMPTION for observed data), and ET0
    is recomputed (FAO-56 eq. 6).
    """
    if fraction <= 0 or day.date.month not in months or day.rain_mm < 1.0:
        return day
    if random.Random(f"{seed}-{day.date.isoformat()}").random() >= fraction:
        return day
    c = contrast or DryDayContrast()
    tmax, tmin = day.tmax + c.tmax, day.tmin + c.tmin
    rhmin = None if day.rhmin is None else max(day.rhmin + c.rh, 15)
    rhmean = None if day.rhmean is None else min(max(day.rhmean + c.rh, 15), 100)
    doy = day.date.timetuple().tm_yday
    if c.rs_ratio is not None and day.rs_mj is not None:
        rs = min(day.rs_mj * c.rs_ratio, 0.75 * extraterrestrial_radiation(lat, doy))
    else:
        rs = rs_hargreaves(tmax, tmin, lat, doy)
    et0 = eto_daily(tmax, tmin, rs, day.u2 if day.u2 is not None else 2.0, lat, doy, elev,
                    rhmax=day.rhmax, rhmin=rhmin, rhmean=rhmean)
    return replace(day, tmax=tmax, tmin=tmin, rain_mm=0.0, rhmin=rhmin, rhmean=rhmean, rs_mj=rs,
                   et0_mm=round(et0, 2), source=f"{day.source}+dry-day")


def warmed(day: WeatherDay, delta_c: float, lat: float = DEMO_LAT, elev: float = DEMO_ELEV_M) -> WeatherDay:
    """Heat scenario: shift Tmax/Tmin, keep relative humidity, wind and radiation; recompute ET0."""
    if delta_c == 0:
        return day
    tmax, tmin = day.tmax + delta_c, day.tmin + delta_c
    doy = day.date.timetuple().tm_yday
    rs = day.rs_mj if day.rs_mj is not None else rs_hargreaves(tmax, tmin, lat, doy)
    et0 = eto_daily(tmax, tmin, rs, day.u2 if day.u2 is not None else 2.0, lat, doy, elev,
                    rhmax=day.rhmax, rhmin=day.rhmin, rhmean=day.rhmean)
    return replace(day, tmax=tmax, tmin=tmin, et0_mm=round(et0, 2), source=f"{day.source}+{delta_c:g}C")


# --------------------------------------------------------------------------- NASA POWER
POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
POWER_PARAMS = ("T2M_MAX", "T2M_MIN", "RH2M", "WS2M", "ALLSKY_SFC_SW_DWN", "PRECTOTCORR")


def power_request_url(lat: float, lon: float, start: date, end: date) -> str:
    q = {
        "parameters": ",".join(POWER_PARAMS),
        "community": "AG",  # AG community returns radiation in MJ m-2 day-1
        "longitude": f"{lon:.4f}",
        "latitude": f"{lat:.4f}",
        "start": start.strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
        "time-standard": "LST",  # day boundaries in local solar time
        "format": "JSON",
    }
    return f"{POWER_URL}?{urllib.parse.urlencode(q)}"


def parse_power_daily(payload: dict, lat: float, elev: float) -> list[WeatherDay]:
    """Parse a POWER daily JSON response. Fill value -999 is marked as a gap.

    Gaps in rain are set to 0 and flagged; gaps in other variables use the previous day.
    """
    params = payload["properties"]["parameter"]
    fill = payload.get("header", {}).get("fill_value", -999.0)
    keys = sorted(params["PRECTOTCORR"].keys())
    out: list[WeatherDay] = []
    last: dict[str, float] = {}
    for k in keys:
        d = datetime.strptime(k, "%Y%m%d").date()
        vals, gap = {}, False
        for p in POWER_PARAMS:
            v = params.get(p, {}).get(k)
            if v is None or v == fill or v <= -998:
                gap = True
                v = 0.0 if p == "PRECTOTCORR" else last.get(p)
            vals[p] = v
            if v is not None:
                last[p] = v
        if any(v is None for v in vals.values()):
            continue  # leading gap with no previous value: drop and leave it visible as a missing date
        doy = d.timetuple().tm_yday
        et0 = eto_daily(vals["T2M_MAX"], vals["T2M_MIN"], vals["ALLSKY_SFC_SW_DWN"], vals["WS2M"],
                        lat, doy, elev, rhmean=vals["RH2M"])
        out.append(WeatherDay(d, vals["T2M_MAX"], vals["T2M_MIN"], vals["PRECTOTCORR"], round(et0, 2),
                              None, None, vals["WS2M"], vals["ALLSKY_SFC_SW_DWN"], "NASA POWER daily (AG)", gap,
                              rhmean=vals["RH2M"]))
    return out


def fetch_power_daily(lat: float, lon: float, start: date, end: date, elev: float, cache: Path) -> list[WeatherDay]:
    """Download (or read the frozen copy of) NASA POWER daily data."""
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"power_{lat:.3f}_{lon:.3f}_{start:%Y%m%d}_{end:%Y%m%d}.json"
    if not path.exists():
        with urllib.request.urlopen(power_request_url(lat, lon, start, end), timeout=60) as r:
            payload = json.load(r)
        payload["_fetched_at"] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(payload))
    return parse_power_daily(json.loads(path.read_text()), lat, elev)


# --------------------------------------------------------------------------- Open-Meteo forecast
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_DAILY = ("precipitation_sum", "et0_fao_evapotranspiration", "temperature_2m_max", "temperature_2m_min")


def open_meteo_request_url(lat: float, lon: float, days: int = 16, tz: str = DEMO_TZ) -> str:
    q = {"latitude": lat, "longitude": lon, "daily": ",".join(OPEN_METEO_DAILY), "timezone": tz,
         "forecast_days": days}
    return f"{OPEN_METEO_URL}?{urllib.parse.urlencode(q)}"


def parse_open_meteo_daily(payload: dict, fetched_at: str) -> list[WeatherDay]:
    daily = payload["daily"]
    out = []
    for i, t in enumerate(daily["time"]):
        vals = [daily[k][i] for k in OPEN_METEO_DAILY]
        gap = any(v is None for v in vals)
        rain, et0, tmax, tmin = (0.0 if v is None else float(v) for v in vals)
        out.append(WeatherDay(date.fromisoformat(t), tmax, tmin, rain, et0,
                              source=f"Open-Meteo forecast fetched {fetched_at}", gap=gap))
    return out


def fetch_open_meteo_forecast(lat: float, lon: float, cache: Path, days: int = 16) -> tuple[list[WeatherDay], str]:
    """Download a forecast once and freeze it with its fetch time (lesson 8 from HydroBrain)."""
    cache.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with urllib.request.urlopen(open_meteo_request_url(lat, lon, days), timeout=60) as r:
        payload = json.load(r)
    payload["_fetched_at"] = fetched_at
    (cache / f"open_meteo_{fetched_at.replace(':', '')}.json").write_text(json.dumps(payload))
    return parse_open_meteo_daily(payload, fetched_at), fetched_at
