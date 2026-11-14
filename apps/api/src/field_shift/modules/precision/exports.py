"""Portable Shapefile Rx and polygon-based ISO11783-10 v4.3 task packages.

The formats carry farmer-confirmed target rates, not a machine/device configuration.
DDIs and scaling follow ISO11783-11 entries 1, 6, 11 at isobus.net.
"""

import csv
import io
import json
from decimal import ROUND_HALF_UP, Decimal
from xml.etree.ElementTree import Element, SubElement, indent, tostring
from zipfile import ZIP_DEFLATED, ZipFile

import shapefile
from shapely.geometry.polygon import orient

from field_shift.modules.precision.geometry import checked_geometry, polygons
from field_shift.modules.precision.models import PrecisionPlan

WGS84 = (
    'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
    'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433],AUTHORITY["EPSG","4326"]]'
)
# factor: displayed rate -> integer ProcessDataValue. VPN scale is its inverse.
RATE_ENCODING = {
    "kg/ha": ("0006", Decimal("100"), "kg_p_ha", 2),
    "seeds/ha": ("000B", Decimal("0.1"), "seeds_p_ha", 0),
    "L/ha": ("0001", Decimal("10000"), "L_p_ha", 4),
}


def encoded_rate(value: float, unit: str) -> int:
    factor = RATE_ENCODING[unit][1]
    result = int((Decimal(str(value)) * factor).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if not 0 <= result <= 2_147_483_647:
        raise ValueError("A rate exceeds the ISOXML integer range for this unit.")
    # Silently rounding a small seeding rate or changing any entered dose is not acceptable.
    if Decimal(result) / factor != Decimal(str(value)):
        raise ValueError(
            "ISOXML requires rates in steps of 0.01 kg/ha, 10 seeds/ha or 0.0001 L/ha."
        )
    return result


def _zip(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


def _metadata(plan: PrecisionPlan, format_name: str) -> bytes:
    return json.dumps(
        {
            "plan_id": plan.id,
            "name": plan.name,
            "field_id": plan.field_id,
            "season": plan.season,
            "format": format_name,
            "boundary_revision": plan.boundary_revision,
            "geometry_fingerprint": plan.geometry_fingerprint,
            "approved_at": plan.approved_at.isoformat(),
            "operation": plan.snapshot["operation"],
            "product": plan.snapshot["product"],
            "unit": plan.snapshot["unit"],
            "provenance": plan.snapshot["provenance"],
            "fallback_rate": plan.snapshot["fallback_rate"],
            "usage": "Select the numeric rate column and confirm units, footprint, "
            "product and controller assignment before use. Zero means application off. "
            "ISOXML position-lost and out-of-field zones are zero. The package has no "
            "device description and requires terminal-side product/channel assignment.",
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")


def shapefile_package(plan: PrecisionPlan) -> bytes:
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    unit = plan.snapshot["unit"]
    rate_column = RATE_ENCODING[unit][2]
    with shapefile.Writer(
        shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON, encoding="utf-8"
    ) as writer:
        writer.field(rate_column, "N", size=18, decimal=4)
        writer.field("ZONE", "C", size=24)
        writer.field("BLOCK", "N", size=4)
        writer.field("UNIT", "C", size=16)
        writer.field("AREA_HA", "N", size=18, decimal=6)
        for item in plan.snapshot["features"]:
            geometry = checked_geometry(item["geometry"])
            rings = []
            for polygon in polygons(geometry):
                normalized = orient(polygon, sign=-1.0)
                rings.append(list(normalized.exterior.coords))
                rings.extend(list(interior.coords) for interior in normalized.interiors)
            writer.poly(rings)
            props = item["properties"]
            # DBF has finite decimal precision; never hide a dose rounding in a machine file.
            value = Decimal(str(props["rate"]))
            if value.quantize(Decimal("0.0001")) != value:
                raise ValueError("Shapefile rate precision is at most four decimal places.")
            writer.record(props["rate"], props["zone"], props.get("block"), unit, props["area_ha"])
    # ASCII basename avoids controller filesystem/encoding ambiguities; UTF-8 names in manifest.
    stem = "Rx/FS_" + plan.id.replace("-", "")[:12]
    return _zip(
        {
            stem + ".shp": shp.getvalue(),
            stem + ".shx": shx.getvalue(),
            stem + ".dbf": dbf.getvalue(),
            stem + ".prj": WGS84.encode(),
            stem + ".cpg": b"UTF-8",
            "manifest.json": _metadata(plan, "Shapefile WGS84 Rx"),
        }
    )


def _xml_polygons(parent: Element, geometry: dict, polygon_type: str) -> None:
    for polygon in polygons(checked_geometry(geometry)):
        pln = SubElement(parent, "PLN", A=polygon_type)
        for index, ring in enumerate([polygon.exterior, *polygon.interiors]):
            line = SubElement(pln, "LSG", A="1" if index == 0 else "2")
            for lon, lat in ring.coords:
                SubElement(line, "PNT", A="2", C=f"{lat:.9f}", D=f"{lon:.9f}")


def isoxml_document(plan: PrecisionPlan) -> bytes:
    snapshot = plan.snapshot
    unit = snapshot["unit"]
    ddi, factor, _, decimals = RATE_ENCODING[unit]
    root = Element(
        "ISO11783_TaskData",
        VersionMajor="4",
        VersionMinor="3",
        ManagementSoftwareManufacturer="Field Shift",
        ManagementSoftwareVersion="0.1",
        DataTransferOrigin="1",
    )
    SubElement(
        root, "VPN", A="VPN1", B="0", C=format(Decimal(1) / factor, "f"), D=str(decimals), E=unit
    )
    SubElement(root, "PDT", A="PDT1", B=snapshot["product"][:32])
    field = SubElement(
        root,
        "PFD",
        A="PFD1",
        C=snapshot["field_name"][:32],
        D=str(round(snapshot["area_ha"] * 10000)),
    )
    _xml_polygons(field, snapshot["footprint"], "1")
    task = SubElement(root, "TSK", A="TSK1", B=plan.name[:32], E="PFD1", G="1", H="1", I="0", J="0")
    for code, name, rate in (
        (0, "Off / no position", 0),
        (1, "Default", snapshot["fallback_rate"]),
    ):
        zone = SubElement(task, "TZN", A=str(code), B=name)
        SubElement(zone, "PDV", A=ddi, B=str(encoded_rate(rate, unit)), C="PDT1", E="VPN1")
    if len(snapshot["features"]) > 253:
        raise ValueError("ISOXML supports at most 253 mapped treatment zones in this profile.")
    for index, item in enumerate(snapshot["features"], 2):
        props = item["properties"]
        zone = SubElement(task, "TZN", A=str(index), B=str(props.get("plot_id") or props["zone"]))
        _xml_polygons(zone, item["geometry"], "2")
        SubElement(zone, "PDV", A=ddi, B=str(encoded_rate(props["rate"], unit)), C="PDT1", E="VPN1")
    indent(root, space="  ")
    return tostring(root, encoding="utf-8", xml_declaration=True)


def isoxml_package(plan: PrecisionPlan) -> bytes:
    return _zip(
        {
            "TASKDATA/TASKDATA.XML": isoxml_document(plan),
            "manifest.json": _metadata(plan, "ISO11783-10 v4.3 polygon treatment zones"),
        }
    )


def report_csv(plan: PrecisionPlan) -> bytes:
    if plan.harvest is None:
        raise ValueError("Import harvest measurements before exporting the report.")
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "field_id",
            "season",
            "plot_id",
            "treatment",
            "rate",
            "unit",
            "block",
            "area_ha",
            "samples",
            "yield_t_ha",
            "sampled_area_ha",
        ]
    )
    for row in plan.harvest["plots"]:
        # Prevent spreadsheet formula execution in user-entered treatment labels.
        name = row["treatment"]
        if name.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
            name = "'" + name
        writer.writerow(
            [
                plan.field_id,
                plan.season,
                row["plot_id"],
                name,
                row["rate"],
                plan.snapshot["unit"],
                row["block"],
                row["area_ha"],
                row["count"],
                row["yield_t_ha"],
                row["sampled_area_ha"],
            ]
        )
    return output.getvalue().encode("utf-8-sig")
