import json
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from os import getenv
from statistics import fmean
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from field_shift.modules.fields.geometry import contains_point
from field_shift.modules.fields.schemas import Polygon
from field_shift.modules.geo_ml.schemas import GeoResult, MoistureCell, MoistureSource

VARIABLE = "soil_moisture_0_to_7cm"
UNIT = "m³/m³"
GRID_STEP = 0.1
ARCHIVE_LAG_DAYS = 6
HISTORICAL_RELEASE_LAG_DAYS = 5
DATASET_URL = "https://doi.org/10.24381/cds.e2161bac"
MAX_RESPONSE_BYTES = 128 * 1024


class MoistureSourceError(RuntimeError):
    """The archive could not provide a complete regional value."""

    public_message = "Soil moisture archive data is temporarily unavailable."


class MoistureRequestRejected(MoistureSourceError):
    """The provider rejected the requested variable combination."""


def latest_archive_day(today: date) -> date:
    return today - timedelta(days=ARCHIVE_LAG_DAYS)


def expected_historical_day(today: date) -> date:
    """Nominal ERA5-Land-T publication lag. Availability is checked in the provider response."""
    return today - timedelta(days=HISTORICAL_RELEASE_LAG_DAYS)


def _fetch(url: str) -> dict:
    try:
        with urlopen(url, timeout=10) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise MoistureSourceError("The moisture archive response exceeds the size limit")
        data = json.loads(raw)
    except HTTPError as error:
        code = error.code
        error.close()
        if code in (400, 422):
            raise MoistureRequestRejected("The moisture archive rejected the request") from None
        raise MoistureSourceError("The moisture archive is unavailable") from None
    except (URLError, OSError, ValueError):
        raise MoistureSourceError("The moisture archive is unavailable") from None
    if not isinstance(data, dict):
        raise MoistureSourceError("The moisture archive returned an invalid document")
    return data


def field_location(geometry: Polygon) -> tuple[float, float]:
    ring = geometry.coordinates[0]
    longitudes, latitudes = zip(*ring, strict=True)
    west, east = min(longitudes), max(longitudes)
    if east - west > 180:
        longitudes = tuple((longitude + 360) % 360 for longitude in longitudes)
        west, east = min(longitudes), max(longitudes)
        ring = list(zip(longitudes, latitudes, strict=True))
    latitude = (min(latitudes) + max(latitudes)) / 2
    longitude = (west + east) / 2
    if not contains_point(ring, longitude, latitude):
        intersections = sorted(
            ax + (bx - ax) * (latitude - ay) / (by - ay)
            for (ax, ay), (bx, by) in zip(ring[:-1], ring[1:], strict=True)
            if (ay > latitude) != (by > latitude)
        )
        # Use the middle of the widest interior segment on the center latitude.
        left, right = max(
            zip(intersections[::2], intersections[1::2], strict=True),
            key=lambda pair: pair[1] - pair[0],
        )
        longitude = (left + right) / 2
    return latitude, (longitude + 180) % 360 - 180


def grid_cells(latitude: float, longitude: float, value: float) -> list[MoistureCell]:
    half = GRID_STEP / 2
    south, north = max(-90, latitude - half), min(90, latitude + half)
    west, east = longitude - half, longitude + half
    if west < -180:
        spans = [(west + 360, 180), (-180, east)]
    elif east > 180:
        spans = [(west, 180), (-180, east - 360)]
    else:
        spans = [(west, east)]
    return [
        MoistureCell(
            geometry=Polygon(
                type="Polygon",
                coordinates=[
                    [(left, south), (right, south), (right, north), (left, north), (left, south)]
                ],
            ),
            value=value,
        )
        for left, right in spans
    ]


def _mean_for_day(
    data: dict,
    day: date,
    requested_latitude: float,
    requested_longitude: float,
    *,
    range_start: date | None = None,
) -> tuple[float, float, float]:
    try:
        latitude = float(data["latitude"])
        longitude = float(data["longitude"])
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError("Invalid archive grid location")
        longitude_distance = abs((longitude - requested_longitude + 180) % 360 - 180)
        if max(abs(latitude - requested_latitude), longitude_distance) > GRID_STEP / 2 + 0.001:
            raise ValueError("Archive grid location is not the nearest cell")
        if data["utc_offset_seconds"] != 0 or data["hourly_units"][VARIABLE] != UNIT:
            raise ValueError("Invalid timezone or moisture units")
        start = datetime.combine(day, time())
        expected_times = [
            (start + timedelta(hours=hour)).isoformat(timespec="minutes") for hour in range(24)
        ]
        hourly = data["hourly"]
        timestamps = hourly["time"]
        values = hourly[VARIABLE]
        if range_start is not None:
            start_index = (day - range_start).days * 24
            timestamps = timestamps[start_index : start_index + 24]
            values = values[start_index : start_index + 24]
        if timestamps != expected_times or len(values) != len(expected_times):
            raise ValueError("Incomplete hourly timestamps")
        if any(type(value) not in (int, float) or not 0 <= value <= 1 for value in values):
            raise ValueError("Missing or invalid hourly moisture")
    except (KeyError, TypeError, ValueError) as error:
        raise MoistureSourceError(
            "The archive did not return a complete 24-hour moisture series for this location"
        ) from error
    return latitude, longitude, round(fmean(values), 3)


class ERA5LandMoistureModel:
    def __init__(self, fetch: Callable[[str], dict] = _fetch) -> None:
        self._fetch = fetch

    def run(self, field_id: str, season: int, geometry: Polygon, day: date) -> GeoResult:
        requested_latitude, requested_longitude = field_location(geometry)
        data = self._fetch_range(requested_latitude, requested_longitude, day, day)
        latitude, longitude, value = _mean_for_day(
            data, day, requested_latitude, requested_longitude
        )
        return self._result(
            field_id,
            season,
            day,
            latitude,
            longitude,
            value,
            requested_latitude,
            requested_longitude,
        )

    def run_latest(self, field_id: str, season: int, geometry: Polygon, today: date) -> GeoResult:
        expected = expected_historical_day(today)
        day = min(expected, date(season, 12, 31))
        if day.year != season:
            raise ValueError("The archive date must be within the selected season")
        if day < expected:
            return self.run(field_id, season, geometry, day)

        # Latest follows the archive release across seasons, including the Dec 31 / Jan 1 window.
        previous = day - timedelta(days=1)
        requested_latitude, requested_longitude = field_location(geometry)
        try:
            data = self._fetch_range(requested_latitude, requested_longitude, previous, day)
        except MoistureRequestRejected:
            # Some archive deployments reject a range ending on an unpublished date.
            return self.run(field_id, previous.year, geometry, previous)
        try:
            latitude, longitude, value = _mean_for_day(
                data, day, requested_latitude, requested_longitude, range_start=previous
            )
        except MoistureSourceError:
            day = previous
            latitude, longitude, value = _mean_for_day(
                data, day, requested_latitude, requested_longitude, range_start=previous
            )
        return self._result(
            field_id,
            day.year,
            day,
            latitude,
            longitude,
            value,
            requested_latitude,
            requested_longitude,
        )

    def _fetch_range(
        self, requested_latitude: float, requested_longitude: float, start: date, end: date
    ) -> dict:
        parameters = {
            "latitude": f"{requested_latitude:.6f}",
            "longitude": f"{requested_longitude:.6f}",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "models": "era5_land",
            "hourly": VARIABLE,
            "timezone": "GMT",
            "cell_selection": "nearest",
            "elevation": "nan",
        }
        key = getenv("OPEN_METEO_API_KEY")
        host = "archive-api.open-meteo.com"
        if key:
            host = "customer-archive-api.open-meteo.com"
            parameters["apikey"] = key
        return self._fetch(f"https://{host}/v1/archive?{urlencode(parameters)}")

    @staticmethod
    def _result(
        field_id: str,
        season: int,
        day: date,
        latitude: float,
        longitude: float,
        value: float,
        requested_latitude: float,
        requested_longitude: float,
    ) -> GeoResult:
        return GeoResult(
            field_id=field_id,
            season=season,
            demo=False,
            date=day,
            depth_cm=(0, 7),
            unit=UNIT,
            minimum=value,
            maximum=value,
            cells=grid_cells(latitude, longitude, value),
            source=MoistureSource(
                name="ERA5-Land",
                provider="Copernicus Climate Change Service via Open-Meteo",
                url=DATASET_URL,
                kind="reanalysis",
                resolution_degrees=GRID_STEP,
                grid_latitude=latitude,
                grid_longitude=longitude,
                sample_count=24,
                requested_latitude=round(requested_latitude, 6),
                requested_longitude=round(requested_longitude, 6),
            ),
        )
