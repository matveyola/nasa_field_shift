"""Small COG windows only; native 20 m SCL controls validity of reflectance pixels."""

from math import ceil, floor, sqrt

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_geom
from shapely.geometry import box, mapping, shape

from field_shift.modules.fields.schemas import Polygon
from field_shift.modules.imagery.spectral import spectral_pixels, summaries
from field_shift.modules.imagery.vegetation_catalog import (
    VegetationScene,
    VegetationUnavailableError,
)
from field_shift.modules.imagery.vegetation_schemas import (
    Heterogeneity,
    VegetationCell,
    VegetationObservation,
)

RESOLUTION_M = 20
MAX_GRID_PIXELS = 65_536
MAX_MAP_CELLS = 2500
MIN_VALID_PIXELS = 10
MIN_VALID_PERCENT = 70


def grid_for(geometry: Polygon, epsg: int):
    projected = transform_geom("EPSG:4326", f"EPSG:{epsg}", geometry.model_dump())
    footprint = shape(projected)
    left, bottom, right, top = footprint.bounds
    # One exterior pixel supplies the cloud/shadow dilation neighbourhood.
    left = floor(left / RESOLUTION_M) * RESOLUTION_M - RESOLUTION_M
    top = ceil(top / RESOLUTION_M) * RESOLUTION_M + RESOLUTION_M
    width = ceil((right - left) / RESOLUTION_M) + 1
    height = ceil((top - bottom) / RESOLUTION_M) + 1
    if width * height > MAX_GRID_PIXELS or max(width, height) > 1024:
        raise ValueError("The field exceeds the supported 20 m analysis window (65,536 pixels).")
    transform = from_origin(left, top, RESOLUTION_M, RESOLUTION_M)
    inside = geometry_mask([projected], (height, width), transform, invert=True)
    if np.count_nonzero(inside) < MIN_VALID_PIXELS:
        raise ValueError("The analysed area contains fewer than ten 20 m pixel centres.")
    return footprint, transform, inside


def read_window(url: str, transform, shape_: tuple, epsg: int, *, categorical: bool):
    height, width = shape_
    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        GDAL_HTTP_CONNECTTIMEOUT="4",
        GDAL_HTTP_TIMEOUT="8",
        GDAL_HTTP_MAX_RETRY="0",
        GDAL_CACHEMAX=16 * 1024 * 1024,
        CPL_VSIL_CURL_CACHE_SIZE=4 * 1024 * 1024,
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
        # Evict this asset's ranges on close so a truncated read cannot poison retries.
        CPL_VSIL_CURL_NON_CACHED=f"/vsicurl/{url}",
        VSI_CACHE=False,
    ):
        with (
            rasterio.open(url) as src,
            WarpedVRT(
                src,
                crs=f"EPSG:{epsg}",
                transform=transform,
                width=width,
                height=height,
                resampling=Resampling.nearest if categorical else Resampling.average,
                nodata=0,
                warp_mem_limit=16,
            ) as vrt,
        ):
            return vrt.read(1, masked=True).filled(0)


def ndvi_pixels(red, nir, scl, inside, red_scale: float, nir_scale: float):
    if not (red.shape == nir.shape == scl.shape == inside.shape):
        raise ValueError("Spectral bands must share one analysis grid.")
    # Dilate all uncertain/cloud/shadow/snow classes by one 20 m pixel. Clear
    # vegetation, bare soil and water remain; water is not silently called missing.
    uncertain = ~np.isin(scl, (4, 5, 6))
    padded = np.pad(uncertain, 1, constant_values=True)
    unsafe = np.zeros_like(uncertain)
    height, width = scl.shape
    for row in range(3):
        for column in range(3):
            unsafe |= padded[row : row + height, column : column + width]
    red_reflectance = np.asarray(red, dtype=np.float64) * red_scale
    nir_reflectance = np.asarray(nir, dtype=np.float64) * nir_scale
    denominator = nir_reflectance + red_reflectance
    valid = (
        inside
        & ~unsafe
        & (red > 0)
        & (nir > 0)
        & np.isfinite(denominator)
        & (denominator > 0.001)
        & (red_reflectance <= 1.5)
        & (nir_reflectance <= 1.5)
    )
    values = np.full(red.shape, np.nan, dtype=np.float64)
    np.divide(nir_reflectance - red_reflectance, denominator, out=values, where=valid)
    valid &= np.isfinite(values) & (values >= -1) & (values <= 1)
    values[~valid] = np.nan
    return values, valid


def msavi_pixels(red, nir, valid, red_scale: float, nir_scale: float):
    """MSAVI2 from scaled reflectance, on the same clear pixels used by NDVI."""
    r = np.asarray(red[valid], dtype=np.float64) * red_scale
    n = np.asarray(nir[valid], dtype=np.float64) * nir_scale
    # Equivalent to the USGS discriminant (2*NIR + 1)² - 8*(NIR - Red).
    values = np.full(red.shape, np.nan, dtype=np.float64)
    values[valid] = (2 * n + 1 - np.sqrt((2 * n - 1) ** 2 + 8 * r)) / 2
    return values


def _cells(values, valid, footprint, transform, epsg, low, high, *, msavi=None, indices=None):
    # Display aggregation keeps response and Leaflet work bounded; statistics are
    # still calculated on every valid native analysis pixel.
    stride = max(1, ceil(sqrt(values.size / MAX_MAP_CELLS)))
    cells = []
    for row in range(0, values.shape[0], stride):
        for column in range(0, values.shape[1], stride):
            block = values[row : row + stride, column : column + stride]
            available = valid[row : row + stride, column : column + stride]
            if not np.any(available):
                continue
            # A coarse block must never paint over hidden clouds or missing native
            # pixels. Omit incomplete display blocks rather than invent coverage.
            if stride > 1 and not np.all(available):
                continue
            value = round(float(np.mean(block[available])), 4)
            msavi_value = None
            spectral_values = {}
            for name, spectral in (indices or {}).items():
                spectral_block = spectral[row : row + stride, column : column + stride]
                # Never paint missing extra-band pixels as if observed.
                if np.all(np.isfinite(spectral_block[available])):
                    spectral_values[name] = round(float(np.mean(spectral_block[available])), 4)
            if msavi is not None:
                msavi_block = msavi[row : row + stride, column : column + stride]
                msavi_value = round(float(np.mean(msavi_block[available])), 4)
            left, top = transform * (column, row)
            right, bottom = transform * (
                min(column + stride, values.shape[1]),
                min(row + stride, values.shape[0]),
            )
            clipped = footprint.intersection(box(left, bottom, right, top))
            pieces = list(clipped.geoms) if clipped.geom_type == "MultiPolygon" else [clipped]
            for piece in pieces:
                if piece.is_empty or piece.geom_type != "Polygon" or piece.area < 1:
                    continue
                # Polygon-only footprints have no holes; split raster cells also have none.
                geometry = transform_geom(f"EPSG:{epsg}", "EPSG:4326", mapping(piece), precision=8)
                cells.append(
                    VegetationCell(
                        geometry=Polygon.model_validate(geometry),
                        value=value,
                        zone="low" if value < low else "high" if value > high else "typical",
                        msavi=msavi_value,
                        indices=spectral_values,
                    )
                )
    return cells, stride * RESOLUTION_M


def analyse_scene(scene: VegetationScene, geometry: Polygon):
    footprint, transform, inside = grid_for(geometry, scene.epsg)
    try:
        scl = read_window(scene.scl_url, transform, inside.shape, scene.epsg, categorical=True)
        # Avoid two reflectance downloads when even the undilated quality mask is too cloudy.
        if np.count_nonzero(inside & np.isin(scl, (4, 5, 6))) / np.count_nonzero(inside) < 0.7:
            return None
        red = read_window(scene.red_url, transform, inside.shape, scene.epsg, categorical=False)
        nir = read_window(scene.nir_url, transform, inside.shape, scene.epsg, categorical=False)
    except (rasterio.errors.RasterioError, OSError):
        raise VegetationUnavailableError(VegetationUnavailableError.public_message) from None
    values, valid = ndvi_pixels(red, nir, scl, inside, scene.red_scale, scene.nir_scale)
    count = int(np.count_nonzero(valid))
    valid_percent = 100 * count / int(np.count_nonzero(inside))
    if count < MIN_VALID_PIXELS or valid_percent < MIN_VALID_PERCENT:
        return None
    observed = values[valid]
    msavi = msavi_pixels(red, nir, valid, scene.red_scale, scene.nir_scale)
    extra_bands = {}
    try:
        for name, (url, scale) in scene.spectral_assets.items():
            extra_bands[name] = (
                np.asarray(
                    read_window(url, transform, inside.shape, scene.epsg, categorical=False),
                    dtype=np.float64,
                )
                * scale
            )
    except (rasterio.errors.RasterioError, OSError):
        # A failed download is retryable, never persisted as a missing spectral index.
        raise VegetationUnavailableError(VegetationUnavailableError.public_message) from None
    indices = spectral_pixels(red * scene.red_scale, nir * scene.nir_scale, valid, extra_bands)
    p10, median, p90 = (float(value) for value in np.percentile(observed, (10, 50, 90)))
    low, high = median - 0.15, median + 0.15
    day = scene.acquired_at.date()
    observation = VegetationObservation(
        date=day,
        acquired_at=scene.acquired_at,
        scene_id=scene.id,
        mean_ndvi=round(float(np.mean(observed)), 4),
        p10_ndvi=round(p10, 4),
        p90_ndvi=round(p90, 4),
        valid_percent=round(valid_percent, 2),
        pixel_count=count,
        catalog_url=scene.catalog_url,
        mean_msavi=round(float(np.mean(msavi[valid])), 4),
        indices=summaries(indices, int(np.count_nonzero(inside))),
    )
    cells, display_resolution = _cells(
        values, valid, footprint, transform, scene.epsg, low, high, msavi=msavi, indices=indices
    )
    heterogeneity = Heterogeneity(
        date=day,
        median_ndvi=round(median, 4),
        spread_ndvi=round(p90 - p10, 4),
        low_area_percent=round(float(np.count_nonzero(observed < low)) / count * 100, 2),
        valid_percent=round(valid_percent, 2),
        low_threshold=round(low, 4),
        high_threshold=round(high, 4),
        resolution_m=display_resolution,
        cells=cells,
    )
    return observation, heterogeneity
