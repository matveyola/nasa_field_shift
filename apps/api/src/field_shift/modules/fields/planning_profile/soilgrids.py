"""SoilGrids 2.0 integer-raster adapter, polygon mask and bounded local cache.

Pinned GeoTIFF/WCS subsets or WebDAV VRT trees are staged by an acquisition
adapter. Never call the paused REST endpoint or guess hydraulic capacities.
"""

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import geometry_mask, geometry_window
from rasterio.warp import transform_geom

from field_shift.contracts.planning_v1.common import SourceRecord
from field_shift.contracts.planning_v1.field import SoilHorizon

from .evidence import reason
from .providers import ProviderResult, check_source

DEPTHS = {(0, 5), (5, 15), (15, 30), (30, 60), (60, 100), (100, 200)}
# SoilGrids mapped units -> contract units. Texture g/kg -> fraction, NOT percent.
SCALES = {
    "sand": (1000, "g/kg"),
    "silt": (1000, "g/kg"),
    "clay": (1000, "g/kg"),
    "soc": (10, "dg/kg"),
    "phh2o": (10, "pH*10"),
}


@dataclass(frozen=True)
class RasterLayer:
    property: str
    top_cm: int
    bottom_cm: int
    unit: str
    path: Path
    source: SourceRecord


def polygon_mean(layer, geometry, max_pixels=250000):
    """Equal-pixel native-grid mean; coverage is of selected polygon pixels.

    Native projected equal-area SoilGrids grid is required. Edge pixels use their
    centers. No promise of precision finer than the source pixel is made.
    """
    if layer.property not in SCALES or layer.unit != SCALES[layer.property][1]:
        raise ValueError("unsupported SoilGrids property or mapped unit")
    if (layer.top_cm, layer.bottom_cm) not in DEPTHS:
        raise ValueError("invalid SoilGrids vertical interval")
    # Streaming checksum bounds memory; input file must be a pinned local asset.
    if layer.path.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("raster asset exceeds 256 MiB bound; stage a subset")
    digest = hashlib.sha256()
    with layer.path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != layer.source.checksum_sha256:
        raise ValueError("raster checksum mismatch")
    with rasterio.open(layer.path) as dataset:
        if dataset.crs is None or not dataset.crs.is_projected:
            raise ValueError("native projected SoilGrids grid required")
        if dataset.crs.to_dict().get("proj") not in {
            "igh",
            "laea",
            "cea",
            "aea",
            "eqearth",
            "moll",
            "sinu",
        }:
            raise ValueError("equal-area grid required for equal-pixel polygon mean")
        if dataset.transform.b or dataset.transform.d:
            raise ValueError("rotated raster requires an explicit resampling adapter")
        polygon = transform_geom("EPSG:4326", dataset.crs, geometry.model_dump(mode="json"))
        # boundless window retains off-raster cells as missing coverage.
        window = geometry_window(dataset, [polygon], boundless=True)
        if window.width * window.height > max_pixels:
            raise ValueError("soil subset exceeds pixel execution bound")
        mask = geometry_mask(
            [polygon],
            out_shape=(int(window.height), int(window.width)),
            transform=dataset.window_transform(window),
            invert=True,
        )
        count = int(mask.sum())
        if count == 0:
            raise ValueError(
                "polygon below native pixel coverage; use representative-point adapter"
            )
        data = dataset.read(1, window=window, boundless=True, masked=True)
        valid = mask & ~np.ma.getmaskarray(data) & np.isfinite(data.data)
        coverage = float(valid.sum() / count)
        if coverage == 0:
            return None, coverage
        values = data.data[valid] / SCALES[layer.property][0]
        if np.any(values < 0):
            raise ValueError("unmasked negative SoilGrids data")
        return float(values.mean()), coverage


class SoilGridsAdapter:
    def __init__(self, layers, *, now, max_cache_age=timedelta(days=30)):
        self.layers, self.now, self.max_cache_age = layers, now, max_cache_age

    def soil(self, geometry, as_of):
        if not self.layers:
            return ProviderResult(
                "insufficient_data",
                None,
                (),
                (
                    reason(
                        "soil_missing",
                        "No pinned SoilGrids assets configured",
                    ),
                ),
            )
        sources, grouped, coverage = {}, {}, []
        try:
            for layer in self.layers:
                check_source(layer.source, as_of, self.now(), self.max_cache_age)
                if layer.source.spatial_method != "area_weighted":
                    raise ValueError("soil raster source must declare area_weighted method")
                value, fraction = polygon_mean(layer, geometry)
                key = (layer.top_cm, layer.bottom_cm)
                values = grouped.setdefault(key, {})
                if layer.property in values:
                    raise ValueError("duplicate depth/property layer")
                values[layer.property] = value
                coverage.append(fraction)
                sources[layer.source.id] = layer.source.model_copy(
                    update={
                        "coverage_fraction": fraction,
                        "quality": "valid" if fraction == 1 else "partial",
                    }
                )
            horizons = []
            for (top, bottom), values in sorted(grouped.items()):
                ids = list(
                    dict.fromkeys(
                        layer.source.id
                        for layer in self.layers
                        if (layer.top_cm, layer.bottom_cm) == (top, bottom)
                    )
                )
                horizons.append(
                    SoilHorizon(
                        top_cm=top,
                        bottom_cm=bottom,
                        sand_fraction=values.get("sand"),
                        silt_fraction=values.get("silt"),
                        clay_fraction=values.get("clay"),
                        organic_carbon_g_kg=values.get("soc"),
                        ph=values.get("phh2o"),
                        field_capacity_m3_m3=None,
                        wilting_point_m3_m3=None,
                        saturation_m3_m3=None,
                        salinity_ds_m=None,
                        source_ids=ids,
                        quality="partial",
                    )
                )
        except (OSError, ValueError, rasterio.errors.RasterioError) as exc:
            return ProviderResult("error", None, (), (reason("soil_source_error", str(exc)),))
        if min(coverage) < 1:
            return ProviderResult(
                "insufficient_data",
                None,
                tuple(sources.values()),
                (
                    reason(
                        "soil_coverage",
                        "Incomplete polygon coverage; NoData is not dry soil",
                    ),
                ),
            )
        return ProviderResult("available", horizons, tuple(sources.values()))
