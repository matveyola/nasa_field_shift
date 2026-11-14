"""Bounded field-scale geometry, measured in a local UTM CRS."""

from math import floor

from rasterio.warp import transform_geom
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import unary_union
from shapely.validation import make_valid


def metric_crs(geometry: dict) -> str:
    polygon = shape(geometry)
    left, bottom, right, top = polygon.bounds
    if right - left > 0.2 or top - bottom > 0.2 or bottom < -80 or top > 84:
        raise ValueError("Precision maps support field footprints up to 0.2 degrees within UTM.")
    centre = polygon.centroid
    zone = min(60, max(1, floor((centre.x + 180) / 6) + 1))
    return f"EPSG:{(32600 if centre.y >= 0 else 32700) + zone}"


def project(geometry: dict, crs: str):
    return shape(transform_geom("EPSG:4326", crs, geometry))


def polygons(geometry) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [] if geometry.is_empty else [geometry]
    return [item for child in getattr(geometry, "geoms", ()) for item in polygons(child)]


def polygonal(geometry):
    return unary_union(polygons(geometry))


def geographic(geometry, crs: str) -> dict:
    projected = shape(transform_geom(crs, "EPSG:4326", mapping(polygonal(geometry))))
    # Reprojection can collapse a narrow gap and leave a self-touching ring.
    # Repair it while preserving the mapped area instead of saving invalid geometry.
    repaired = polygonal(make_valid(projected))
    if repaired.is_empty:
        raise ValueError("The projected map has no polygonal area.")
    return dict(mapping(repaired))


def feature(geometry, crs: str, **properties) -> dict:
    return {
        "type": "Feature",
        "geometry": geographic(geometry, crs),
        "properties": {**properties, "area_ha": geometry.area / 10_000},
    }


def checked_geometry(geometry: dict):
    result = shape(geometry)
    if not isinstance(result, (Polygon, MultiPolygon)) or not result.is_valid or result.is_empty:
        raise ValueError("The saved map contains invalid polygon geometry.")
    return result
