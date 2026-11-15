"""Reference imagery and point selection for the field boundary model."""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from http.client import HTTPException
from logging import getLogger
from math import cos, radians
from os import getenv
from pathlib import Path
from threading import BoundedSemaphore
from time import monotonic
from typing import Literal
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from warnings import catch_warnings, filterwarnings

import numpy as np
import rasterio
from field_shift_science.fields.geometry import contains_point
from field_shift_science.fields.schemas import Polygon
from pydantic import BaseModel
from rasterio.enums import Resampling
from rasterio.errors import NotGeoreferencedWarning, RasterioError
from rasterio.features import shapes
from rasterio.io import MemoryFile
from rasterio.transform import Affine, array_bounds, from_bounds, from_origin, xy
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform, transform_geom
from rasterio.windows import Window
from shapely.geometry import Point, mapping, shape
from shapely.geometry import Polygon as ShapelyPolygon

from field_shift.modules.imagery.sentinel import (
    CatalogUnavailableError,
    SatelliteImage,
    satellite_image_at,
)

CHIP_SIZE = 256
CHIP_CENTER = CHIP_SIZE // 2
RESOLUTION_M = 10
MASK_BYTES = CHIP_SIZE * CHIP_SIZE // 8
MAX_MASKS = 6
MODEL_TIMEOUT_SECONDS = 10
# Web Mercator envelope of apps/web/public/bondurant-usgs-aerial.webp.
DEMO_BOUNDS = (-93.42280910459, 41.69526025718, -93.38719090357, 41.71673794856)
DEMO_EPSG = 3857
DEMO_WINDOW_SIZES = (350, 480, 260)
USGS_EXPORT = (
    "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/export"
)
USGS_IMAGE_TIMEOUT_SECONDS = 5
IMAGE_MAX_BYTES = 1024 * 1024
AERIAL_WINDOW_METERS = 1600
EOX_WMS = "https://tiles.maps.eox.at/map"
EOX_IMAGE_TIMEOUT_SECONDS = 9
EOX_WINDOW_METERS = 2560
EOX_WIDE_WINDOW_METERS = 3840
NATIONAL_ORTHOPHOTOS = (
    (
        "IGN France orthophoto",
        "https://data.geopf.fr/wms-r",
        "ORTHOIMAGERY.ORTHOPHOTOS",
        "1.3.0",
        ((-5.2, 41.2, 8.4, 51.2),),
    ),
    (
        "PNOA Spain orthophoto",
        "https://www.ign.es/wms-inspire/pnoa-ma",
        "OI.OrthoimageCoverage",
        "1.1.1",
        ((-9.5, 35.8, 4.5, 43.9), (-18.3, 27.5, -13.3, 29.6)),
    ),
    (
        "GeoSN Saxony orthophoto",
        "https://geodienste.sachsen.de/wms_geosn_dop-rgb/guest",
        "sn_dop_020",
        "1.1.1",
        ((11.86, 50.17, 15.04, 51.7),),
    ),
)
MAX_CONTOUR_AREA_CHANGE = 0.03
SEARCH_WINDOW_SECONDS = 42
_slots = BoundedSemaphore(2)
_inference_slot = BoundedSemaphore(1)
logger = getLogger(__name__)

Chip = tuple[np.ndarray, np.ndarray, Affine]
FailureReason = Literal["no_imagery", "no_contour", "model_unavailable", "busy"]


class BoundarySuggestion(BaseModel):
    geometry: Polygon | None = None
    source: str = "Delineate Anything v2"
    imagery_date: date | None = None
    imagery_source: str | None = None
    failure_reason: FailureReason | None = None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _valid_prediction_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.hostname)
        and value.isascii()
        and not any(character.isspace() for character in value)
        and (port is None or port > 0)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def read_chip(scene: SatelliteImage, longitude: float, latitude: float) -> Chip:
    crs = f"EPSG:{scene.epsg}"
    xs, ys = transform("EPSG:4326", crs, [longitude], [latitude])
    # The clicked position sits at the center of pixel (128, 128).
    radius = (CHIP_CENTER + 0.5) * RESOLUTION_M
    grid = from_origin(xs[0] - radius, ys[0] + radius, RESOLUTION_M, RESOLUTION_M)
    with (
        rasterio.Env(
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            GDAL_HTTP_CONNECTTIMEOUT="3",
            GDAL_HTTP_TIMEOUT="5",
            GDAL_HTTP_MAX_RETRY="0",
            GDAL_CACHEMAX=16 * 1024 * 1024,
            CPL_VSIL_CURL_CACHE_SIZE=4 * 1024 * 1024,
            CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
            CPL_VSIL_CURL_NON_CACHED=f"/vsicurl/{scene.visual_url}",
            VSI_CACHE=False,
        ),
        rasterio.open(scene.visual_url) as source,
        WarpedVRT(
            source,
            crs=crs,
            transform=grid,
            width=CHIP_SIZE,
            height=CHIP_SIZE,
            resampling=Resampling.bilinear,
            src_nodata=0,
            nodata=0,
            warp_mem_limit=16,
        ) as window,
    ):
        rgb = window.read([1, 2, 3], out_dtype="uint8")
        valid = np.any(rgb != 0, axis=0)
    return np.moveaxis(rgb, 0, -1), valid, grid


def read_demo_chip(path: Path, longitude: float, latitude: float, span: int) -> Chip | None:
    west, south, east, north = DEMO_BOUNDS
    if not (west < longitude < east and south < latitude < north) or not path.is_file():
        return None
    xs, ys = transform(
        "EPSG:4326", f"EPSG:{DEMO_EPSG}", [west, east, longitude], [south, north, latitude]
    )
    # The WEBP has no georeferencing metadata. Its envelope supplies the transform.
    with catch_warnings():
        filterwarnings("ignore", category=NotGeoreferencedWarning)
        with rasterio.open(path) as image:
            grid = from_bounds(xs[0], ys[0], xs[1], ys[1], image.width, image.height)
            column, row = (~grid) @ (xs[2], ys[2])
            offset = span * (CHIP_CENTER + 0.5) / CHIP_SIZE
            left, top = column - offset, row - offset
            right, bottom = left + span, top + span
            rgb = image.read(
                [1, 2, 3],
                window=Window(left, top, span, span),
                out_shape=(3, CHIP_SIZE, CHIP_SIZE),
                out_dtype="uint8",
                resampling=Resampling.average,
                boundless=True,
                fill_value=0,
            )
    x_left, y_top = grid @ (left, top)
    x_right, y_bottom = grid @ (right, bottom)
    chip_grid = from_bounds(x_left, y_bottom, x_right, y_top, CHIP_SIZE, CHIP_SIZE)
    return np.moveaxis(rgb, 0, -1), np.any(rgb != 0, axis=0), chip_grid


def read_usgs_chip(longitude: float, latitude: float) -> Chip | None:
    coverage = ((-125, 24, -66, 50), (-161, 18, -154, 23), (-179, 51, -129, 72))
    if not any(w <= longitude <= e and s <= latitude <= n for w, s, e, n in coverage):
        return None
    grid = mercator_grid(longitude, latitude)
    query = urlencode(
        {
            "bbox": ",".join(map(str, array_bounds(CHIP_SIZE, CHIP_SIZE, grid))),
            "bboxSR": 3857,
            "imageSR": 3857,
            "size": f"{CHIP_SIZE},{CHIP_SIZE}",
            "format": "png32",
            "transparent": "true",
            "f": "image",
        }
    )
    return read_image_chip(f"{USGS_EXPORT}?{query}", grid, USGS_IMAGE_TIMEOUT_SECONDS)


def mercator_grid(longitude: float, latitude: float, width_m: int = AERIAL_WINDOW_METERS) -> Affine:
    xs, ys = transform("EPSG:4326", "EPSG:3857", [longitude], [latitude])
    resolution = width_m / CHIP_SIZE / cos(radians(latitude))
    radius = (CHIP_CENTER + 0.5) * resolution
    return from_origin(xs[0] - radius, ys[0] + radius, resolution, resolution)


def read_cloudless_chip(
    longitude: float, latitude: float, width_m: int = EOX_WINDOW_METERS
) -> Chip | None:
    grid = mercator_grid(longitude, latitude, width_m)
    return read_wms_chip(EOX_WMS, "s2cloudless_3857", grid, "image/jpeg")


def read_wms_chip(
    endpoint: str, layer: str, grid: Affine, image_type: str, version: str = "1.1.1"
) -> Chip | None:
    query = urlencode(
        {
            "SERVICE": "WMS",
            "VERSION": version,
            "REQUEST": "GetMap",
            "LAYERS": layer,
            "STYLES": "",
            "CRS" if version == "1.3.0" else "SRS": "EPSG:3857",
            "BBOX": ",".join(map(str, array_bounds(CHIP_SIZE, CHIP_SIZE, grid))),
            "WIDTH": CHIP_SIZE,
            "HEIGHT": CHIP_SIZE,
            "FORMAT": image_type,
            "TRANSPARENT": "TRUE" if image_type == "image/png" else "FALSE",
        }
    )
    return read_image_chip(f"{endpoint}?{query}", grid, EOX_IMAGE_TIMEOUT_SECONDS)


def read_image_chip(url: str, grid: Affine, timeout: int) -> Chip | None:
    drivers = {"image/png": "PNG", "image/jpeg": "JPEG"}
    request = Request(url, headers={"Accept": "image/png, image/jpeg"})
    with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
        driver = drivers.get(response.headers.get_content_type())
        if not driver:
            return None
        body = response.read(IMAGE_MAX_BYTES + 1)
    if len(body) > IMAGE_MAX_BYTES:
        return None
    with catch_warnings():
        filterwarnings("ignore", category=NotGeoreferencedWarning)
        with MemoryFile(body) as data, data.open(driver=driver) as image:
            if (image.width, image.height) != (CHIP_SIZE, CHIP_SIZE) or image.count not in (3, 4):
                return None
            pixels = image.read()
    rgb = pixels[:3]
    valid = pixels[3] >= 240 if len(pixels) == 4 else np.max(rgb, axis=0) > 4
    return np.moveaxis(rgb, 0, -1), valid, grid


def predict_masks(url: str, rgb: np.ndarray) -> list[np.ndarray]:
    request = Request(
        url,
        data=rgb.tobytes(),
        headers={"Content-Type": "application/octet-stream", "Accept": "application/octet-stream"},
        method="POST",
    )
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    with opener.open(request, timeout=MODEL_TIMEOUT_SECONDS) as response:
        if response.headers.get_content_type() != "application/octet-stream":
            raise OSError("Unexpected boundary model content type")
        body = response.read(MASK_BYTES * MAX_MASKS + 1)
    if not body:
        return []
    if len(body) > MASK_BYTES * MAX_MASKS or len(body) % MASK_BYTES:
        raise OSError("Unexpected boundary model mask size")
    packed = np.frombuffer(body, dtype=np.uint8).reshape(-1, MASK_BYTES)
    return list(np.unpackbits(packed, axis=1).reshape(-1, CHIP_SIZE, CHIP_SIZE))


def contour_at(
    mask: np.ndarray, grid: Affine, epsg: int, longitude: float, latitude: float
) -> Polygon | None:
    if not mask[CHIP_CENTER, CHIP_CENTER]:
        return None
    x, y = xy(grid, CHIP_CENTER, CHIP_CENTER)
    seed = Point(x, y)
    for coordinates, _ in shapes(mask, mask=mask.astype(bool), transform=grid):
        candidate = shape(coordinates)
        if not candidate.is_valid or not candidate.contains(seed):
            continue
        left, bottom, right, top = candidate.bounds
        # Reject the clicked component when its contour is cut by the image window.
        outer_left, outer_bottom, outer_right, outer_top = array_bounds(*mask.shape, grid)
        if left <= outer_left or right >= outer_right or top >= outer_top or bottom <= outer_bottom:
            continue
        # The field editor accepts one exterior ring. Fill only small gaps in the
        # raster mask; keep substantial excluded areas out of the suggestion.
        original_area = candidate.area
        shell = ShapelyPolygon(candidate.exterior)
        filled_area = shell.area - original_area
        area_budget = original_area * MAX_CONTOUR_AREA_CHANGE
        if filled_area > area_budget:
            continue
        # Remove raster stair steps while preserving the area and location of the outline.
        pixel = max(grid.a, -grid.e)
        for tolerance in (2 * pixel, pixel, 0.5 * pixel, 0):
            reduced = shell.simplify(tolerance, preserve_topology=True)
            if len(reduced.exterior.coords) > 128 or not reduced.contains(seed):
                continue
            if tolerance and filled_area + shell.symmetric_difference(reduced).area > area_budget:
                continue
            try:
                result = Polygon.model_validate(
                    transform_geom(f"EPSG:{epsg}", "EPSG:4326", mapping(reduced), precision=8)
                )
            except ValueError:
                continue
            if contains_point(result.coordinates[0], longitude, latitude):
                return result
    return None


def _mask_contour(
    masks: list[np.ndarray],
    valid: np.ndarray,
    grid: Affine,
    epsg: int,
    position: tuple[float, float],
) -> Polygon | None:
    # A mask ending next to missing pixels may have been cut by the source image.
    missing = ~valid
    near_missing = missing.copy()
    near_missing[1:] |= missing[:-1]
    near_missing[:-1] |= missing[1:]
    near_missing[:, 1:] |= missing[:, :-1]
    near_missing[:, :-1] |= missing[:, 1:]
    for mask in masks:
        if np.any(mask & near_missing):
            continue
        polygon = contour_at(mask, grid, epsg, *position)
        if polygon is not None:
            return polygon
    return None


def _chips_at(
    longitude: float,
    latitude: float,
    deadline: float,
) -> Iterator[tuple[Chip, int, str, date | None]]:
    demo_image = getenv("FIELD_SHIFT_BOUNDARY_DEMO_IMAGE", "").strip()
    if demo_image:
        for span in DEMO_WINDOW_SIZES:
            if monotonic() >= deadline:
                return
            try:
                chip = read_demo_chip(Path(demo_image), longitude, latitude, span)
            except (OSError, RasterioError):
                logger.debug("Could not read bundled USGS imagery", exc_info=True)
                break
            if chip is not None:
                yield chip, DEMO_EPSG, "USGS aerial imagery", None

    if monotonic() >= deadline:
        return
    for name, endpoint, layer, version, extents in NATIONAL_ORTHOPHOTOS:
        if not any(w <= longitude <= e and s <= latitude <= n for w, s, e, n in extents):
            continue
        try:
            chip = read_wms_chip(
                endpoint, layer, mercator_grid(longitude, latitude), "image/png", version
            )
        except (HTTPException, OSError, RasterioError):
            logger.debug("Could not read %s", name, exc_info=True)
        else:
            if chip is not None:
                yield chip, DEMO_EPSG, name, None

    if monotonic() >= deadline:
        return
    try:
        aerial = read_usgs_chip(longitude, latitude)
    except (HTTPException, OSError, RasterioError):
        logger.debug("Could not read USGS imagery", exc_info=True)
    else:
        if aerial is not None:
            yield aerial, DEMO_EPSG, "USGS aerial imagery", None

    if monotonic() >= deadline:
        return
    try:
        scene = satellite_image_at(longitude, latitude, datetime.now(UTC).date())
        if scene is not None:
            chip = read_chip(scene, longitude, latitude)
            yield chip, scene.epsg, "Copernicus Sentinel-2", scene.acquired_at.date()
    except (CatalogUnavailableError, HTTPException, OSError, RasterioError):
        logger.debug("Could not read Sentinel-2 imagery", exc_info=True)

    # The wider crop changes the context around the click and keeps large fields in frame.
    for width in (EOX_WINDOW_METERS, EOX_WIDE_WINDOW_METERS):
        if monotonic() >= deadline:
            return
        try:
            chip = read_cloudless_chip(longitude, latitude, width)
        except (HTTPException, OSError, RasterioError):
            logger.debug("Could not read EOxCloudless imagery at %s m", width, exc_info=True)
            continue
        if chip is not None:
            yield chip, DEMO_EPSG, "EOxCloudless 2016", None


def suggest_boundary(longitude: float, latitude: float) -> BoundarySuggestion | None:
    url = getenv("FIELD_SHIFT_BOUNDARY_MODEL_URL", "").strip()
    if not url:
        return None
    if not _valid_prediction_url(url):
        return BoundarySuggestion(failure_reason="model_unavailable")
    if not _slots.acquire(blocking=False):
        return BoundarySuggestion(failure_reason="busy")
    reason: FailureReason = "no_imagery"
    try:
        deadline = monotonic() + SEARCH_WINDOW_SECONDS
        for (rgb, valid, grid), epsg, source, acquired in _chips_at(longitude, latitude, deadline):
            if not valid[CHIP_CENTER, CHIP_CENTER] or np.mean(valid) < 0.5:
                continue
            if not _inference_slot.acquire(timeout=3):
                return BoundarySuggestion(failure_reason="busy")
            try:
                masks = predict_masks(url, rgb)
            except HTTPError as exc:
                logger.debug("Boundary inference HTTP response: %s", exc.code, exc_info=True)
                # A timed-out request can leave the previous inference finishing.
                reason = "busy" if exc.code == 429 else "model_unavailable"
                return BoundarySuggestion(failure_reason=reason)
            except (HTTPException, OSError):
                logger.debug("Boundary inference request failed", exc_info=True)
                return BoundarySuggestion(failure_reason="model_unavailable")
            finally:
                _inference_slot.release()
            polygon = _mask_contour(masks, valid, grid, epsg, (longitude, latitude))
            if polygon is not None:
                return BoundarySuggestion(
                    geometry=polygon, imagery_date=acquired, imagery_source=source
                )
            reason = "no_contour"
            logger.debug(
                "No usable contour from %s (%d masks covering the click)", source, len(masks)
            )
        return BoundarySuggestion(failure_reason=reason)
    finally:
        _slots.release()
