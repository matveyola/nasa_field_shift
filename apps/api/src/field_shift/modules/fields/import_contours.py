"""Explicit offline ingestion of licensed NASA Harvest Ukraine contour archives.

Never downloads or executes a supplied URL. No archive paths are extracted.
Example: python -m field_shift.modules.fields.import_contours archive.zip
  --region Zakarpattia --year 2023 --bbox 22.25 48.4 22.45 48.6
The administrator supplies the database through normal application configuration.
"""

import argparse
import hashlib
from contextlib import ExitStack
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import shapefile
from field_shift_science.fields.geometry import area_hectares
from field_shift_science.fields.schemas import Polygon
from rasterio.warp import transform_geom
from shapely.geometry import mapping, shape
from sqlalchemy import select

from field_shift.config import get_settings
from field_shift.db import build_session_factory
from field_shift.modules.fields.models import PreparedContour

SOURCE = "NASA Harvest · Sadeh et al. (2025)"
SOURCE_URL = "https://data.harvestportal.org/dataset/ukraine-field-boundary-delineation-"


def simplify_contour(geometry: dict, source_crs: str) -> tuple[Polygon, float] | None:
    geographic = transform_geom(source_crs, "EPSG:4326", geometry)
    original = shape(geographic)
    if original.geom_type != "Polygon" or not original.is_valid or original.interiors:
        return None
    longitude, latitude = original.centroid.coords[0]
    if not (21 <= longitude <= 41 and 44 <= latitude <= 53):
        return None
    epsg = 32600 + int((longitude + 180) / 6) + 1
    metric = shape(transform_geom("EPSG:4326", f"EPSG:{epsg}", geographic))
    for tolerance in (0, 2, 5, 10, 15, 20):
        reduced = metric.simplify(tolerance, preserve_topology=True)
        if reduced.geom_type != "Polygon" or len(reduced.exterior.coords) > 128:
            continue
        if abs(reduced.area - metric.area) / metric.area > 0.02:
            return None
        converted = transform_geom(f"EPSG:{epsg}", "EPSG:4326", mapping(reduced), precision=8)
        try:
            polygon = Polygon.model_validate(converted)
        except ValueError:
            return None
        return polygon, tolerance
    return None


def ingest_archive(
    factory, archive: Path, region: str, year: int, bbox: tuple[float, ...], limit=2000
):
    if year not in (2021, 2022, 2023) or not region.strip() or len(region) > 80:
        raise ValueError("Choose a published Harvest Ukraine year and region.")
    west, south, east, north = bbox
    if not (
        21 <= west < east <= 41
        and 44 <= south < north <= 53
        and east - west <= 0.5
        and north - south <= 0.5
        and 1 <= limit <= 5000
    ):
        raise ValueError("Use a bounded Ukraine window of at most 0.5° and at most 5,000 contours.")
    if archive.stat().st_size > 1200 * 1024 * 1024:
        raise ValueError("Archive exceeds the supported size.")
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    counts = {"read": 0, "imported": 0, "existing": 0, "rejected": 0, "truncated": False}
    with ZipFile(archive) as zipped, ExitStack() as stack:
        entries = zipped.infolist()
        if len(entries) > 32 or sum(item.file_size for item in entries) > 3 * 1024**3:
            raise ValueError("Archive contents exceed bounded extraction limits.")
        components = {}
        for suffix in (".shp", ".shx", ".dbf", ".prj"):
            matching = [item for item in entries if Path(item.filename).suffix.lower() == suffix]
            if len(matching) != 1:
                raise ValueError("Archive must contain exactly one Shapefile dataset.")
            maximum = 16_000 if suffix == ".prj" else 32 * 1024 * 1024
            if suffix == ".shp":
                # Stream forward through the compressed geometry without a second
                # several-hundred-MB copy on disk. No archive paths are extracted.
                components[suffix] = stack.enter_context(zipped.open(matching[0]))
            else:
                if matching[0].file_size > maximum:
                    raise ValueError("Shapefile metadata exceeds its memory limit.")
                components[suffix] = BytesIO(zipped.read(matching[0]))
        crs = components[".prj"].read().decode("utf-8-sig")
        with (
            shapefile.Reader(
                shp=components[".shp"], shx=components[".shx"], dbf=components[".dbf"]
            ) as reader,
            factory.begin() as session,
        ):
            if reader.numRecords > 1_000_000:
                raise ValueError("Archive contains too many shapes.")
            for ordinal, feature in enumerate(reader.iterShapes()):
                # Skip by source bounding box before materialising dense coordinate rings.
                extent = transform_geom(
                    crs,
                    "EPSG:4326",
                    {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [feature.bbox[0], feature.bbox[1]],
                                [feature.bbox[2], feature.bbox[1]],
                                [feature.bbox[2], feature.bbox[3]],
                                [feature.bbox[0], feature.bbox[3]],
                                [feature.bbox[0], feature.bbox[1]],
                            ]
                        ],
                    },
                )
                bounds = shape(extent).bounds
                if bounds[2] < west or bounds[0] > east or bounds[3] < south or bounds[1] > north:
                    continue
                if counts["read"] >= limit:
                    counts["truncated"] = True
                    break
                counts["read"] += 1
                if len(feature.points) > 100_000:
                    counts["rejected"] += 1
                    continue
                parsed = simplify_contour(feature.__geo_interface__, crs)
                if parsed is None:
                    counts["rejected"] += 1
                    continue
                polygon, _tolerance = parsed
                identifier = f"harvest-{year}-{digest.hexdigest()[:16]}-{ordinal}"
                if session.scalar(
                    select(PreparedContour.id).where(PreparedContour.id == identifier)
                ):
                    counts["existing"] += 1
                    continue
                ring = polygon.coordinates[0]
                xs, ys = zip(*ring, strict=True)
                session.add(
                    PreparedContour(
                        id=identifier,
                        geometry=polygon.model_dump(mode="json"),
                        area_ha=area_hectares(ring),
                        west=min(xs),
                        east=max(xs),
                        south=min(ys),
                        north=max(ys),
                        year=year,
                        source=SOURCE,
                        source_url=SOURCE_URL + str(year),
                        license="CC BY",
                        region=region,
                        imported_at=datetime.now(UTC),
                    )
                )
                counts["imported"] += 1
    return counts | {"archive_sha256": digest.hexdigest(), "bbox": list(bbox)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--region", required=True)
    parser.add_argument("--year", type=int, choices=(2021, 2022, 2023), required=True)
    parser.add_argument("--bbox", type=float, nargs=4, required=True)
    parser.add_argument("--limit", type=int, default=2000)
    args = parser.parse_args()
    print(
        ingest_archive(
            build_session_factory(get_settings().database_url),
            args.archive,
            args.region,
            args.year,
            tuple(args.bbox),
            args.limit,
        )
    )
