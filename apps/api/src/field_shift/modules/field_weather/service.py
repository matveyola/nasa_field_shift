import hashlib
import json
from calendar import monthrange
from datetime import UTC, datetime, timedelta

from field_shift_science.fields.schemas import Polygon
from field_shift_science.geo_ml.era5_land import field_location

from . import providers
from .calculations import forecast_periods, power_days, rainfall_period
from .schemas import RainHistory, WeatherSummary


def scope(field_id, revision, geometry, now):
    latitude, longitude = field_location(Polygon.model_validate(geometry))
    return {
        "field_id": field_id,
        "boundary_revision": revision,
        "geometry_fingerprint": hashlib.sha256(
            json.dumps(geometry, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "latitude": round(latitude, 4),
        "longitude": round(longitude, 4),
        "generated_at": now,
    }


def summary(field_id, revision, geometry, *, now=None):
    now = now or datetime.now(UTC)
    context = scope(field_id, revision, geometry, now)
    payload, url, retrieved = providers.forecast(context["latitude"], context["longitude"])
    try:
        values = forecast_periods(payload, now)
        return WeatherSummary(
            **context,
            **values,
            sources=[
                {
                    "provider": "Open-Meteo",
                    "url": url,
                    "retrieved_at": retrieved,
                    "description": "Grid forecast, not a weather station on the field. "
                    "Temperature °C, precipitation mm, gusts m/s. Local field timezone.",
                }
            ],
        )
    except (KeyError, ValueError, TypeError, OverflowError) as error:
        raise providers.WeatherUnavailable(
            "Weather forecast response is incomplete or invalid"
        ) from error


def history(field_id, revision, geometry, *, typical=False, now=None):
    now = now or datetime.now(UTC)
    context = scope(field_id, revision, geometry, now)
    yesterday = now.astimezone(UTC).date() - timedelta(days=1)
    start = yesterday - timedelta(days=59)
    payload, url, retrieved = providers.archive(
        context["latitude"], context["longitude"], start, yesterday
    )
    try:
        values = {d: v for d, v in power_days(payload).items() if start <= d <= yesterday}
        end = max((d for d, value in values.items() if value is not None), default=None)
        sources = [
            {
                "provider": "NASA POWER",
                "url": url,
                "retrieved_at": retrieved,
                "description": "PRECTOTCORR corrected precipitation, mm/day, community AG, "
                "UTC completed days; a gridded archive, not field measurements.",
            }
        ]
        historical = None
        if typical and end is not None:
            earliest = end.replace(
                year=end.year - 10, day=min(end.day, monthrange(end.year - 10, end.month)[1])
            ) - timedelta(days=29)
            latest = end.replace(
                year=end.year - 1, day=min(end.day, monthrange(end.year - 1, end.month)[1])
            )
            past, past_url, past_retrieved = providers.archive(
                context["latitude"], context["longitude"], earliest, latest, True
            )
            historical = {d: v for d, v in power_days(past).items() if earliest <= d <= latest}
            sources.append(
                {
                    "provider": "NASA POWER",
                    "url": past_url,
                    "retrieved_at": past_retrieved,
                    "description": "Retrospective archive baseline: median of complete "
                    "same-length periods in previous ten anchor years.",
                }
            )
        return RainHistory(
            **context,
            requested_end=yesterday,
            available_end=end,
            archive_lag_days=(yesterday - end).days if end else None,
            periods=[rainfall_period(values, end, length, historical) for length in (7, 30)]
            if end
            else [],
            sources=sources,
            limitations=[
                "Trailing unavailable days establish archive lag; internal gaps are never skipped.",
                "Typical median uses complete periods; sample count is explicit, not assumed ten.",
                "February 29 anchors clamp to February 28; each period retains 7 or 30 days.",
                "Historical values may be revised; this is not a vintage-preserved hindcast.",
                "An archive with no valid value in the last 60 days is unavailable.",
            ],
        )
    except (KeyError, ValueError, TypeError, OverflowError) as error:
        raise providers.WeatherUnavailable(
            "Rainfall archive response is incomplete or invalid"
        ) from error
