import json
import re
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from math import cos, radians
from time import time
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel

from field_shift.modules.fields.geometry import contains_point
from field_shift.modules.fields.schemas import Polygon

EARLIEST_SCENE = date(2015, 6, 23)
SEARCH_DAYS = 35
MAX_TILE_CLOUD_PERCENT = 20
MAX_CATALOG_BYTES = 2 * 1024 * 1024
CATALOG = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
ASSET_HOSTS = {
    "sentinel-cogs.s3.us-west-2.amazonaws.com",
    "e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com",
}
BOUNDS = tuple[float, float, float, float]
POINT_PREVIEW_RADIUS_DEGREES = 0.0135
POINT_CACHE_STEP_DEGREES = 0.002


class CatalogUnavailableError(RuntimeError):
    """The imagery catalog did not return a usable response."""


class SatelliteImage(BaseModel):
    id: str
    acquired_at: datetime
    on_or_before: date
    tile_cloud_percent: float
    visual_url: str
    catalog_url: str
    epsg: int
    bounds: BOUNDS


class SatelliteScene(SatelliteImage):
    field_id: str


def preview_bounds(geometry: Polygon) -> BOUNDS | None:
    longitudes, latitudes = zip(*geometry.coordinates[0], strict=True)
    west, south, east, north = min(longitudes), min(latitudes), max(longitudes), max(latitudes)
    latitude_scale = cos(radians((south + north) / 2))
    if north > 84 or south < -80 or east - west > 0.5:
        return None
    # Bound the browser's native-resolution COG window, including some farm context.
    if max((east - west) * latitude_scale, north - south) > 0.06:
        return None
    padding = 0.009
    return (
        max(-180, west - padding / latitude_scale),
        max(-80, south - padding),
        min(180, east + padding / latitude_scale),
        min(84, north + padding),
    )


def _fetch_catalog(url: str) -> list[dict]:
    request = Request(url, headers={"Accept": "application/geo+json, application/json"})
    try:
        with urlopen(request, timeout=8) as response:
            body = response.read(MAX_CATALOG_BYTES + 1)
        if len(body) > MAX_CATALOG_BYTES:
            raise ValueError("Catalog response too large")
        features = json.loads(body)["features"]
        if not isinstance(features, list):
            raise ValueError("Catalog features must be an array")
    except (URLError, OSError, ValueError, KeyError, TypeError):
        raise CatalogUnavailableError(
            "The Sentinel-2 catalog is temporarily unavailable."
        ) from None
    return features


def _covers_field(footprint: dict, bbox: BOUNDS) -> bool:
    west, south, east, north = bbox
    coordinates = footprint["coordinates"]
    polygons = coordinates if footprint["type"] == "MultiPolygon" else [coordinates]
    return any(
        all(contains_point(rings[0], lon, lat) for lon in (west, east) for lat in (south, north))
        for rings in polygons
        if rings
    )


def _search_start(cutoff: date) -> date:
    return max(EARLIEST_SCENE, cutoff - timedelta(days=SEARCH_DAYS - 1))


def _candidate(item: dict, field_bbox: BOUNDS, cutoff: date) -> dict | None:
    try:
        item_id = item["id"]
        props = item["properties"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", item_id):
            return None
        acquired_at = datetime.fromisoformat(props["datetime"].replace("Z", "+00:00"))
        cloud = float(props["eo:cloud_cover"])
        epsg = int(props.get("proj:epsg") or props["proj:code"].removeprefix("EPSG:"))
        visual_url = item["assets"]["visual"]["href"]
        url = urlsplit(visual_url)
        if not (
            item["collection"] == COLLECTION
            and acquired_at.tzinfo is not None
            and _search_start(cutoff) <= acquired_at.astimezone(UTC).date() <= cutoff
            and 0 <= cloud <= MAX_TILE_CLOUD_PERCENT
            and (32601 <= epsg <= 32660 or 32701 <= epsg <= 32760)
            and url.scheme == "https"
            and url.netloc in ASSET_HOSTS
            and not url.query
            and not url.fragment
            and re.fullmatch(r"/[A-Za-z0-9/_-]+/TCI\.tif", url.path)
            and _covers_field(item["geometry"], field_bbox)
        ):
            return None
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError):
        return None
    return {
        "id": item_id,
        "acquired_at": acquired_at,
        "tile_cloud_percent": cloud,
        "visual_url": visual_url,
        "catalog_url": f"{CATALOG}/collections/{COLLECTION}/items/{item_id}",
        "epsg": epsg,
    }


@lru_cache(maxsize=256)
def _search(field_bbox: BOUNDS, cutoff: date, _cache_window: int) -> dict | None:
    start = _search_start(cutoff)
    query = urlencode(
        {
            "collections": COLLECTION,
            "bbox": ",".join(f"{coord:.8f}" for coord in field_bbox),
            "datetime": f"{start}T00:00:00Z/{cutoff}T23:59:59.999Z",
            "limit": 40,
            "sortby": "-properties.datetime",
        }
    )
    scenes = [
        scene
        for item in _fetch_catalog(f"{CATALOG}/search?{query}")
        if (scene := _candidate(item, field_bbox, cutoff)) is not None
    ]
    return max(
        scenes, key=lambda scene: (scene["acquired_at"], -scene["tile_cloud_percent"]), default=None
    )


def satellite_scene(field_id: str, geometry: Polygon, cutoff: date) -> SatelliteScene | None:
    bounds = preview_bounds(geometry)
    if bounds is None:
        return None
    longitudes, latitudes = zip(*geometry.coordinates[0], strict=True)
    field_bbox = (min(longitudes), min(latitudes), max(longitudes), max(latitudes))
    scene = _search(field_bbox, cutoff, int(time()) // (15 * 60))
    if scene is None:
        return None
    return SatelliteScene(field_id=field_id, on_or_before=cutoff, bounds=bounds, **scene)


def satellite_image_at(longitude: float, latitude: float, cutoff: date) -> SatelliteImage | None:
    # Nearby clicks share a bounded catalog lookup for a roughly 3 km preview window.
    step = POINT_CACHE_STEP_DEGREES
    center_lon = round(longitude / step) * step
    center_lat = round(latitude / step) * step
    latitude_radius = POINT_PREVIEW_RADIUS_DEGREES
    longitude_radius = latitude_radius / cos(radians(center_lat))
    bounds = (
        max(-180, center_lon - longitude_radius),
        max(-80, center_lat - latitude_radius),
        min(180, center_lon + longitude_radius),
        min(84, center_lat + latitude_radius),
    )
    scene = _search(bounds, cutoff, int(time()) // (15 * 60))
    if scene is None:
        return None
    return SatelliteImage(on_or_before=cutoff, bounds=bounds, **scene)
