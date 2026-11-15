"""Missing-aware calendar arithmetic; no provider calls or crop assumptions."""

from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from math import isfinite
from statistics import median
from zoneinfo import ZoneInfo


def number(value, *, lower=None, upper=None, fill=None):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    if not isfinite(value) or value == fill:
        return None
    if lower is not None and value < lower or upper is not None and value > upper:
        return None
    return float(value)


def days(start, end):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def power_days(payload):
    if (
        payload.get("header", {}).get("time_standard") != "UTC"
        or payload.get("parameters", {}).get("PRECTOTCORR", {}).get("units") != "mm/day"
    ):
        raise ValueError("Archive must use UTC days and mm/day")
    fill = payload.get("header", {}).get("fill_value", -999)
    raw = payload["properties"]["parameter"]["PRECTOTCORR"]
    result = {}
    for key, value in raw.items():
        day = datetime.strptime(key, "%Y%m%d").date()
        result[day] = number(value, lower=0, fill=fill)
    return result


def rainfall_period(values, end, length, historical=None):
    start = end - timedelta(days=length - 1)
    dates = days(start, end)
    missing = [d for d in dates if values.get(d) is None]
    total = None if missing else sum(values[d] for d in dates)
    samples = []
    if historical is not None:
        for year in range(end.year - 10, end.year):
            anchor = date(year, end.month, min(end.day, monthrange(year, end.month)[1]))
            first = anchor - timedelta(days=length - 1)
            selected = days(first, anchor)
            if all(historical.get(d) is not None for d in selected):
                samples.append(
                    {"start": first, "end": anchor, "rain_mm": sum(historical[d] for d in selected)}
                )
    typical = median(s["rain_mm"] for s in samples) if samples else None
    # An incomplete actual period never acquires a comparison, even with a valid baseline.
    difference = total - typical if total is not None and typical is not None else None
    return {
        "length_days": length,
        "start": start,
        "end": end,
        "complete": not missing,
        "missing_dates": missing,
        "rain_mm": total,
        "dry_days": None if missing else sum(values[d] < 1 for d in dates),
        "typical_rain_mm": typical,
        "difference_mm": difference,
        "typical_periods": samples,
        "typical_status": "not_requested"
        if historical is None
        else "available"
        if samples
        else "unavailable",
    }


def forecast_periods(payload, now):
    expected = {
        "temperature_2m": "°C",
        "precipitation": "mm",
        "wind_gusts_10m": "m/s",
        "time": "unixtime",
    }
    daily_units = {
        "precipitation_sum": "mm",
        "precipitation_probability_max": "%",
        "time": "unixtime",
    }
    if any(payload.get("hourly_units", {}).get(k) != v for k, v in expected.items()) or any(
        payload.get("daily_units", {}).get(k) != v for k, v in daily_units.items()
    ):
        raise ValueError("Forecast units differ from the requested contract")
    zone = ZoneInfo(payload["timezone"])
    today = now.astimezone(zone).date()
    hourly = payload["hourly"]
    hours = []
    times = hourly["time"]
    if len(set(times)) != len(times) or times != sorted(times):
        raise ValueError("Hourly forecast times must be unique and chronological")
    for i, timestamp in enumerate(times):
        instant = datetime.fromtimestamp(timestamp, UTC)
        if not now <= instant < now + timedelta(hours=24):
            continue

        def value(name, **bounds):
            values = hourly.get(name, [])
            return number(values[i], **bounds) if i < len(values) else None

        temperature = value("temperature_2m", lower=-100, upper=65)
        hours.append(
            {
                "time": instant.astimezone(zone),
                "temperature_c": temperature,
                "rain_mm": value("precipitation", lower=0),
                "gust_ms": value("wind_gusts_10m", lower=0),
                "below_zero": temperature < 0 if temperature is not None else None,
            }
        )
    daily = payload["daily"]
    by_date = {}
    for i, timestamp in enumerate(daily["time"]):
        day = datetime.fromtimestamp(timestamp, UTC).astimezone(zone).date()
        if day in by_date:
            raise ValueError("Daily forecast dates must be unique")

        def daily_value(name, **bounds):
            values = daily.get(name, [])
            return number(values[i], **bounds) if i < len(values) else None

        by_date[day] = {
            "date": day,
            "rain_mm": daily_value("precipitation_sum", lower=0),
            "rain_probability_percent": daily_value(
                "precipitation_probability_max", lower=0, upper=100
            ),
        }
    expected = days(today + timedelta(days=1), today + timedelta(days=5))
    selected = [by_date[d] for d in expected if d in by_date]
    complete_rain = len(selected) == 5 and all(d["rain_mm"] is not None for d in selected)
    complete_probability = len(selected) == 5 and all(
        d["rain_probability_percent"] is not None for d in selected
    )
    rain_sum = sum(d["rain_mm"] for d in selected) if complete_rain else None
    max_rain = max(d["rain_mm"] for d in selected) if complete_rain else None
    least = min(d["rain_probability_percent"] for d in selected) if complete_probability else None
    return {
        "timezone": payload["timezone"],
        "hours": hours,
        "hours_complete": len(hours) == 24
        and all(
            (b["time"].timestamp() - a["time"].timestamp()) == 3600
            for a, b in zip(hours, hours[1:])
        )
        and all(d[k] is not None for d in hours for k in ("temperature_c", "rain_mm", "gust_ms")),
        "forecast_days": selected,
        "requested_start": expected[0],
        "requested_end": expected[-1],
        "missing_dates": [d for d in expected if d not in by_date],
        "forecast_rain_mm": rain_sum,
        "wettest_dates": [
            d["date"] for d in selected if max_rain is not None and d["rain_mm"] == max_rain
        ],
        "lowest_probability_dates": [
            d["date"]
            for d in selected
            if least is not None and d["rain_probability_percent"] == least
        ],
    }
