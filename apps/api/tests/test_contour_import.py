from io import BytesIO
from zipfile import ZipFile

import pytest
import shapefile
from sqlalchemy import select

from field_shift.db import build_session_factory
from field_shift.models import metadata
from field_shift.modules.fields.import_contours import ingest_archive, simplify_contour
from field_shift.modules.fields.models import PreparedContour

WGS84 = (
    'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
    'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]'
)


def archive(tmp_path, *, duplicate=False):
    shp, shx, dbf = BytesIO(), BytesIO(), BytesIO()
    with shapefile.Writer(shp=shp, shx=shx, dbf=dbf) as writer:
        writer.field("id", "N")
        writer.poly([[[22.3, 48.5], [22.3, 48.51], [22.31, 48.51], [22.31, 48.5], [22.3, 48.5]]])
        writer.record(1)
    path = tmp_path / "harvest-test.zip"
    with ZipFile(path, "w") as zipped:
        for suffix, buffer in (("shp", shp), ("shx", shx), ("dbf", dbf)):
            zipped.writestr("../../unsafe-name." + suffix, buffer.getvalue())
        zipped.writestr("../../unsafe-name.prj", WGS84)
        if duplicate:
            zipped.writestr("second.prj", WGS84)
    return path


def test_streamed_archive_has_no_path_extraction_and_is_idempotent(tmp_path):
    factory = build_session_factory("sqlite://")
    metadata.create_all(factory.kw["bind"])
    source = archive(tmp_path)
    first = ingest_archive(factory, source, "Test region", 2023, (22.25, 48.4, 22.45, 48.6))
    assert first["imported"] == 1 and first["rejected"] == 0
    assert len(first["archive_sha256"]) == 64
    repeated = ingest_archive(factory, source, "Test region", 2023, (22.25, 48.4, 22.45, 48.6))
    assert repeated["existing"] == 1 and repeated["imported"] == 0
    with factory() as session:
        contour = session.scalar(select(PreparedContour))
        assert contour.region == "Test region" and contour.year == 2023
        assert contour.license == "CC BY"
        assert contour.area_ha > 0
    assert sorted(path.name for path in tmp_path.iterdir()) == ["harvest-test.zip"]
    factory.kw["bind"].dispose()


def test_contour_import_rejects_ambiguous_components_and_unsupported_shapes(tmp_path):
    factory = build_session_factory("sqlite://")
    with pytest.raises(ValueError, match="exactly one"):
        ingest_archive(
            factory, archive(tmp_path, duplicate=True), "Test", 2023, (22.25, 48.4, 22.45, 48.6)
        )
    with pytest.raises(ValueError, match="bounded Ukraine"):
        ingest_archive(factory, tmp_path / "absent.zip", "Test", 2023, (21, 44, 41, 53))
    outer = [[22.3, 48.5], [22.31, 48.5], [22.31, 48.51], [22.3, 48.51], [22.3, 48.5]]
    hole = [
        [22.302, 48.502],
        [22.303, 48.502],
        [22.303, 48.503],
        [22.302, 48.503],
        [22.302, 48.502],
    ]
    assert simplify_contour({"type": "Polygon", "coordinates": [outer, hole]}, WGS84) is None
    factory.kw["bind"].dispose()
