"""Bounded provider reads, per-process LRU caching and same-key request coalescing.

At most two network reads concurrently; one retry of a transient failure, no retry
sleep. Socket timeout 12s per attempt, JSON <=2MB. Cache128 entries, <=32MB total,
30min forecast/6h recent archive/24h historical. No import-time I/O or credentials.
"""

import json
from collections import OrderedDict
from datetime import UTC, datetime
from threading import BoundedSemaphore, Lock
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

MAX_BYTES = 2_000_000
_CACHE = OrderedDict()
_LOCK = Lock()
_FLIGHTS = {}
_SLOTS = BoundedSemaphore(2)


class WeatherUnavailable(ValueError):
    pass


def read_json(url):
    for attempt in range(2):
        try:
            with urlopen(
                Request(url, headers={"User-Agent": "FieldShift-weather/1.0"}), timeout=12
            ) as response:
                raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise WeatherUnavailable("Weather response exceeds the download bound")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise WeatherUnavailable("Weather response must be a JSON object")
            return payload
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt:
                raise WeatherUnavailable("Weather provider is temporarily unavailable") from error
        except (URLError, TimeoutError, OSError) as error:
            if attempt:
                raise WeatherUnavailable("Weather provider is temporarily unavailable") from error
        except (ValueError, KeyError) as error:
            raise WeatherUnavailable("Weather provider returned an invalid response") from error


def cached_json(url, ttl):
    key = (url, ttl)
    with _LOCK:
        item = _CACHE.get(key)
        if item and monotonic() < item[0]:
            _CACHE.move_to_end(key)
            return item[1], item[3]
        if key not in _FLIGHTS and len(_FLIGHTS) >= 128:
            raise WeatherUnavailable("Weather service is busy; retry later")
        flight, users = _FLIGHTS.get(key, (Lock(), 0))
        _FLIGHTS[key] = (flight, users + 1)
    acquired = flight.acquire(timeout=26)
    try:
        if not acquired:
            raise WeatherUnavailable("Weather request is still running; retry later")
        with _LOCK:
            item = _CACHE.get(key)
            if item and monotonic() < item[0]:
                return item[1], item[3]
        if not _SLOTS.acquire(blocking=False):
            raise WeatherUnavailable("Weather service is busy; retry later")
        try:
            payload = read_json(url)
        finally:
            _SLOTS.release()
        size = len(json.dumps(payload))
        with _LOCK:
            _CACHE[key] = (monotonic() + ttl, payload, size, datetime.now(UTC))
            retrieved = _CACHE[key][3]
            _CACHE.move_to_end(key)
            while len(_CACHE) > 128 or sum(v[2] for v in _CACHE.values()) > 32_000_000:
                _CACHE.popitem(last=False)
        return payload, retrieved
    finally:
        if acquired:
            flight.release()
        with _LOCK:
            lock, users = _FLIGHTS[key]
            if users == 1:
                del _FLIGHTS[key]
            else:
                _FLIGHTS[key] = (lock, users - 1)


def forecast(latitude, longitude):
    url = "https://api.open-meteo.com/v1/forecast?" + urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": "auto",
            "hourly": "temperature_2m,precipitation,wind_gusts_10m",
            "daily": "precipitation_sum,precipitation_probability_max",
            "wind_speed_unit": "ms",
            "timeformat": "unixtime",
            "forecast_days": 7,
        }
    )
    payload, retrieved = cached_json(url, 1800)
    return payload, url, retrieved


def archive(latitude, longitude, start, end, historical=False):
    if end < start or (end - start).days > 3700:
        raise WeatherUnavailable("Archive period exceeds its execution bound")
    url = "https://power.larc.nasa.gov/api/temporal/daily/point?" + urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "start": start.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"),
            "parameters": "PRECTOTCORR",
            "community": "AG",
            "format": "JSON",
            "time-standard": "UTC",
        }
    )
    payload, retrieved = cached_json(url, 86400 if historical else 21600)
    return payload, url, retrieved
