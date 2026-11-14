"""Read small, native-detail DEM windows; never download a complete degree tile."""

from dataclasses import dataclass
from math import ceil, floor
from time import monotonic

import numpy as np
import rasterio
from affine import Affine
from field_shift_science.fields.schemas import Polygon
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.transform import from_origin
from rasterio.warp import reproject, transform_bounds
from rasterio.windows import Window, from_bounds

GRID_METERS = 30
MAX_GRID_SIDE = 256
MAX_SOURCE_SIDE = 1024
MAX_TILES = 4
MAX_FETCH_SECONDS = 75
SOURCE_ROOT = "https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com"


class TerrainUnavailableError(RuntimeError):
    """Transient provider failure: the durable job may retry."""


class TerrainCoverageError(ValueError):
    """Unsupported or missing coverage: no invented elevations."""


@dataclass(frozen=True)
class TerrainGrid:
    elevation: np.ndarray
    transform: Affine
    epsg: int
    source_tiles: tuple[str, ...]


def tile_url(longitude: int, latitude: int) -> str:
    if not (-180 <= longitude < 180 and -90 <= latitude < 90):
        raise ValueError("Invalid DEM tile coordinates.")
    north = f"{'N' if latitude >= 0 else 'S'}{abs(latitude):02d}_00"
    east = f"{'E' if longitude >= 0 else 'W'}{abs(longitude):03d}_00"
    name = f"Copernicus_DSM_COG_10_{north}_{east}_DEM"
    return f"{SOURCE_ROOT}/{name}/{name}.tif"


def target_grid(geometry: Polygon) -> tuple[int, Affine, tuple[int, int]]:
    longitudes, latitudes = zip(*geometry.coordinates[0], strict=True)
    west, south, east, north = min(longitudes), min(latitudes), max(longitudes), max(latitudes)
    if south < -79 or north > 83 or east - west > 1:
        raise TerrainCoverageError("Terrain directions are unavailable for this location.")
    longitude, latitude = (west + east) / 2, (south + north) / 2
    zone = min(60, max(1, floor((longitude + 180) / 6) + 1))
    epsg = (32600 if latitude >= 0 else 32700) + zone
    left, bottom, right, top = transform_bounds("EPSG:4326", epsg, west, south, east, north)
    # Three pixels of context ensure derivatives are supported near the field boundary.
    left = floor(left / GRID_METERS) * GRID_METERS - 3 * GRID_METERS
    top = ceil(top / GRID_METERS) * GRID_METERS + 3 * GRID_METERS
    width = ceil((right - left) / GRID_METERS) + 3
    height = ceil((top - bottom) / GRID_METERS) + 3
    if width > MAX_GRID_SIDE or height > MAX_GRID_SIDE:
        raise TerrainCoverageError("Terrain preview supports fields up to about 7 km across.")
    return epsg, from_origin(left, top, GRID_METERS, GRID_METERS), (height, width)


def _read_tile(url: str, bounds: tuple[float, float, float, float]) -> tuple:
    try:
        # GDAL's process-wide HTTP range cache survives ordinary dataset.close().
        # A truncated response can otherwise poison every later Celery attempt.
        # Set this only while opening this DEM: closing the handle (also on an
        # exception) evicts its ranges, without flushing other datasets' caches.
        # Persistent complete results are still reused from PostgreSQL.
        with (
            rasterio.Env(CPL_VSIL_CURL_NON_CACHED=f"/vsicurl/{url}"),
            rasterio.open(url) as dataset,
        ):
            if dataset.crs != rasterio.crs.CRS.from_epsg(4326):
                raise TerrainUnavailableError("The elevation source has an unexpected projection.")
            if dataset.count != 1 or dataset.transform.a <= 0 or dataset.transform.e >= 0:
                raise TerrainUnavailableError("The elevation source has an unexpected grid.")
            requested = from_bounds(*bounds, transform=dataset.transform)
            left, top = floor(requested.col_off) - 1, floor(requested.row_off) - 1
            right = ceil(requested.col_off + requested.width) + 1
            bottom = ceil(requested.row_off + requested.height) + 1
            window = Window(left, top, right - left, bottom - top).intersection(
                Window(0, 0, dataset.width, dataset.height)
            )
            if window.width > MAX_SOURCE_SIDE or window.height > MAX_SOURCE_SIDE:
                raise TerrainCoverageError("The elevation source window exceeds the preview limit.")
            values = dataset.read(1, window=window, masked=True).astype("float32").filled(np.nan)
            values[(values < -500) | (values > 9000)] = np.nan
            return values, dataset.window_transform(window)
    except RasterioIOError as error:
        if "404" in str(error) or "403" in str(error):
            raise TerrainCoverageError(
                "No public Copernicus elevation tile covers this field."
            ) from None
        raise TerrainUnavailableError("The elevation source is temporarily unavailable.") from None


def load_terrain_grid(geometry: Polygon) -> TerrainGrid:
    epsg, affine, shape = target_grid(geometry)
    height, width = shape
    bounds = transform_bounds(
        epsg,
        "EPSG:4326",
        affine.c,
        affine.f - height * GRID_METERS,
        affine.c + width * GRID_METERS,
        affine.f,
    )
    west, south, east, north = bounds
    tile_coordinates = [
        (longitude, latitude)
        for longitude in range(floor(west), floor(east) + 1)
        for latitude in range(floor(south), floor(north) + 1)
    ]
    if len(tile_coordinates) > MAX_TILES:
        raise TerrainCoverageError("Too many elevation tiles are needed for this preview.")
    merged = np.full(shape, np.nan, dtype="float32")
    sources: list[str] = []
    deadline = monotonic() + MAX_FETCH_SECONDS
    # At most four small source windows. Limit GDAL caches, requests and warp memory.
    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
        GDAL_HTTP_TIMEOUT="8",
        GDAL_HTTP_CONNECTTIMEOUT="5",
        GDAL_HTTP_MAX_RETRY="0",
        GDAL_CACHEMAX=16 * 1024 * 1024,
        VSI_CACHE=False,
        CPL_VSIL_CURL_CACHE_SIZE=4 * 1024 * 1024,
    ):
        for longitude, latitude in tile_coordinates:
            if monotonic() > deadline:
                raise TerrainUnavailableError("The elevation source exceeded the processing limit.")
            url = tile_url(longitude, latitude)
            values, source_transform = _read_tile(url, bounds)
            projected = np.full(shape, np.nan, dtype="float32")
            reproject(
                values,
                projected,
                src_transform=source_transform,
                src_crs="EPSG:4326",
                src_nodata=np.nan,
                dst_transform=affine,
                dst_crs=epsg,
                dst_nodata=np.nan,
                resampling=Resampling.bilinear,
                num_threads=1,
                warp_mem_limit=8,
            )
            valid = np.isfinite(projected)
            merged[valid] = projected[valid]
            sources.append(url)
    return TerrainGrid(merged, affine, epsg, tuple(sources))
