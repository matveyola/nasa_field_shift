import csv
import io
from datetime import UTC, datetime
from uuid import uuid4
from xml.etree.ElementTree import fromstring
from zipfile import ZipFile

import pytest
import shapefile
from fastapi.testclient import TestClient
from rasterio.warp import transform_geom
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

from field_shift.api import create_app
from field_shift.db import build_session_factory
from field_shift.jobs import repository
from field_shift.jobs.models import JobKind, JobStatus
from field_shift.models import metadata
from field_shift.modules.fields.models import Field, FieldPlanting
from field_shift.modules.imagery.vegetation import vegetation_footprint
from field_shift.modules.imagery.vegetation_schemas import METHOD_VERSION
from field_shift.modules.precision.exports import encoded_rate
from field_shift.modules.precision.geometry import geographic, metric_crs, project
from field_shift.modules.precision.schemas import TrialCreate
from field_shift.modules.precision.trials import design_trial, harvest_report, parse_harvest
from field_shift.seed import seed_demo

GEOMETRY = mapping(box(31.0, 48.5, 31.01, 48.51))


@pytest.fixture
def client():
    factory = build_session_factory("sqlite://")
    metadata.create_all(factory.kw["bind"])
    with factory() as session:
        seed_demo(session)
        field = session.get(Field, "F-03")
        field.geometry = dict(GEOMETRY)
        session.commit()
    with TestClient(create_app(session_factory=factory)) as http:
        yield http, factory
    factory.kw["bind"].dispose()


def trial_payload(client):
    context = client.get("/api/fields/F-03/precision/context?season=2026").json()
    return {
        "client_id": str(uuid4()),
        "name": "Nitrogen trial",
        "product": "Fertiliser",
        "season": 2026,
        "boundary_revision": context["boundary_revision"],
        "geometry_fingerprint": context["geometry_fingerprint"],
        "operation": "fertilization",
        "fallback_rate": 100,
        "treatments": [{"name": "Control", "rate": 100}, {"name": "Reduced", "rate": 80}],
        "replicates": 3,
        "strip_width_m": 12,
        "heading_degrees": 25,
        "headland_m": 12,
        "seed": 42,
    }


def create_trial(client):
    response = client.post("/api/fields/F-03/precision/trials", json=trial_payload(client))
    assert response.status_code == 201, response.text
    return response.json()


def approve(client, plan, **extra):
    response = client.post(
        f"/api/fields/F-03/precision/{plan['id']}/approve",
        json={"version": plan["version"], "confirmed_rates_and_geometry": True, **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_trial_is_repeatable_bounded_and_has_every_treatment_in_every_block(client):
    http, _ = client
    payload = TrialCreate.model_validate(trial_payload(http))
    crs = metric_crs(GEOMETRY)
    features = design_trial(GEOMETRY, crs, payload)
    assert features == design_trial(GEOMETRY, crs, payload)
    projected = [project(item["geometry"], crs) for item in features]
    field = project(GEOMETRY, crs)
    assert abs(unary_union(projected).area - field.area) < 0.01
    assert abs(sum(poly.area for poly in projected) - field.area) < 0.01
    for block in (1, 2, 3):
        strips = [item for item in features if item["properties"].get("block") == block]
        assert {item["properties"]["treatment"] for item in strips} == {"Control", "Reduced"}
        assert all(field.buffer(0.001).covers(project(item["geometry"], crs)) for item in strips)
    with pytest.raises(ValueError, match="too narrow"):
        design_trial(
            GEOMETRY, crs, payload.model_copy(update={"strip_width_m": 100, "replicates": 12})
        )


def test_saved_plan_idempotency_scope_confirmation_and_revision_fence(client):
    http, factory = client
    payload = trial_payload(http)
    first = http.post("/api/fields/F-03/precision/trials", json=payload).json()
    retry = http.post("/api/fields/F-03/precision/trials", json=payload)
    assert retry.status_code == 201
    assert retry.json()["id"] == first["id"]
    assert len(http.get("/api/fields/F-03/precision?season=2026").json()) == 1
    assert (
        http.post(
            "/api/fields/F-03/precision/trials", json={**payload, "name": "Changed"}
        ).status_code
        == 409
    )
    base = f"/api/fields/F-03/precision/{first['id']}"
    assert http.get(base + "/export/shapefile").status_code == 409
    assert http.get(base.replace("F-03", "F-01")).status_code == 404
    confirmed = approve(http, first)
    assert confirmed["version"] == 2
    assert http.get(base + "/export/shapefile").status_code == 200
    with factory() as session:
        field = session.get(Field, "F-03")
        field.boundary_revision += 1
        session.commit()
    assert http.get(base).json()["stale"] is True
    assert http.get(base + "/export/shapefile").status_code == 409


def test_planting_change_without_boundary_revision_invalidates_export(client):
    http, factory = client
    plan = approve(http, create_trial(http))
    with factory() as session:
        session.add(
            FieldPlanting(
                field_id="F-03",
                season=2026,
                area_ha=10,
                geometry=dict(mapping(box(31, 48.5, 31.005, 48.505))),
            )
        )
        session.commit()
    assert http.get(f"/api/fields/F-03/precision/{plan['id']}/export/isoxml").status_code == 409


def test_machine_exports_roundtrip_geometry_rates_units_and_explicit_zero(client):
    http, _ = client
    payload = trial_payload(http)
    payload["fallback_rate"] = 0
    plan = http.post("/api/fields/F-03/precision/trials", json=payload).json()
    base = f"/api/fields/F-03/precision/{plan['id']}"
    response = http.post(
        base + "/approve", json={"version": 1, "confirmed_rates_and_geometry": True}
    )
    assert response.status_code == 422
    approve(http, plan, confirmed_zero_rates=True)
    archive = ZipFile(io.BytesIO(http.get(base + "/export/shapefile").content))

    def part(extension):
        return io.BytesIO(
            archive.read(next(name for name in archive.namelist() if name.endswith(extension)))
        )

    with shapefile.Reader(shp=part(".shp"), shx=part(".shx"), dbf=part(".dbf")) as reader:
        assert len(reader) == 7
        assert {row[0] for row in reader.records()} == {0, 80, 100}
        assert reader.fields[1][0] == "kg_p_ha"
        assert reader.bbox == pytest.approx([31, 48.5, 31.01, 48.51], abs=1e-8)
        geometry = unary_union([shape(record.__geo_interface__) for record in reader.shapes()])
        assert geometry.symmetric_difference(shape(GEOMETRY)).area < 1e-11
    archive = ZipFile(io.BytesIO(http.get(base + "/export/isoxml").content))
    root = fromstring(archive.read("TASKDATA/TASKDATA.XML"))
    assert root.attrib["VersionMajor"] == "4"
    assert root.find("VPN").attrib["C"] == "0.01"
    assert {node.attrib["A"] for node in root.findall(".//PDV")} == {"0006"}
    assert {node.attrib["B"] for node in root.findall(".//PDV")} == {"0", "8000", "10000"}
    task = root.find("TSK")
    assert task.attrib["I"] == task.attrib["J"] == "0"
    point = root.find("PFD/PLN/LSG/PNT")
    assert 48.5 <= float(point.attrib["C"]) <= 48.51
    assert 31 <= float(point.attrib["D"]) <= 31.01
    data = http.get(base + "/export/geojson").json()
    assert "crs" not in data
    assert data["features"][0]["geometry"]["type"] in {"Polygon", "MultiPolygon"}


@pytest.mark.parametrize(
    ("rate", "unit", "expected"),
    [
        (100, "kg/ha", 10000),
        (70000, "seeds/ha", 7000),
        (200, "L/ha", 2000000),
        (0, "kg/ha", 0),
        (1.01, "kg/ha", 101),
    ],
)
def test_isoxml_encodes_published_ddi_units(rate, unit, expected):
    assert encoded_rate(rate, unit) == expected


def test_isoxml_rejects_rounding_or_overflow():
    for rate, unit in [(70001, "seeds/ha"), (1.001, "kg/ha"), (1e7, "L/ha")]:
        with pytest.raises(ValueError):
            encoded_rate(rate, unit)


def test_harvest_report_uses_blocks_not_pseudoreplicated_points_and_zero_is_real(client):
    http, _ = client
    plan = approve(http, create_trial(http))
    features = plan["snapshot"]["features"]
    rows = ["longitude,latitude,yield_t_ha"]
    expected_means = []
    for item in features:
        props = item["properties"]
        if props.get("plot_id"):
            point = shape(item["geometry"]).representative_point()
            value = props["block"] - 1 + (2 if props["zone"] == "T2" else 0)
            rows.append(f"{point.x},{point.y},{value}")
            if props["zone"] == "T1":
                expected_means.append(value)
    rows.append("0,0,999")
    url = f"/api/fields/F-03/precision/{plan['id']}/harvest"
    body = {"version": plan["version"], "file_name": "harvest.csv", "csv": "\n".join(rows)}
    assert http.post(url, json=body).status_code == 422
    body["csv"] = "\n".join(rows[:-1] + ["0,0,3"])
    response = http.post(url, json=body)
    assert response.status_code == 200, response.text
    report = response.json()["harvest"]
    assert report["excluded_samples"] == 1
    assert report["baseline"] == "Control"
    control = next(row for row in report["treatments"] if row["treatment"] == "Control")
    reduced = next(row for row in report["treatments"] if row["treatment"] == "Reduced")
    assert control["yield_t_ha"] == 1
    assert reduced["difference_t_ha"] == 2
    assert reduced["paired_blocks"] == 3
    assert reduced["difference_standard_error"] == 0
    assert http.post(url, json=body).json()["version"] == response.json()["version"]
    exported = http.get(url.replace("/harvest", "/export/csv"))
    assert len(list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))) == 6


def test_harvest_rejects_duplicate_nonfinite_and_bad_area_and_excludes_border():
    for text in [
        "longitude,latitude,yield_t_ha\n31,48,nan",
        "longitude,latitude,yield_t_ha\n31,48,0\n31,48,2",
        "longitude,latitude,yield_t_ha,area_ha\n31,48,0,0",
        "longitude,latitude,yield_t_ha\n31,48,-1",
    ]:
        with pytest.raises(ValueError):
            parse_harvest(text)
    features = [
        {
            "geometry": mapping(box(0, 0, 1, 1)),
            "properties": {
                "plot_id": "B1-T1",
                "zone": "T1",
                "treatment": "control",
                "block": 1,
                "rate": 100,
                "area_ha": 1,
            },
        }
    ]
    rows = parse_harvest("longitude,latitude,yield_t_ha\n0,0,0\n0.5,0.5,0")
    result = harvest_report(features, rows, "EPSG:32631")
    assert result["excluded_samples"] == 1
    assert result["plots"][0]["yield_t_ha"] == 0


def test_prescription_loads_exact_saved_scene_and_fills_unmapped_area(client):
    http, factory = client
    with factory() as session:
        field = session.get(Field, "F-03")
        _, fingerprint = vegetation_footprint(session, field, 2026)
        job = repository.submit(
            session,
            kind=JobKind.GEO_ML,
            field_id="F-03",
            inputs={
                "analysis": "vegetation",
                "season": 2026,
                "vegetation_mask_hash": fingerprint,
                "sample_date": "2026-07-31",
            },
            boundary_revision=0,
            idempotency_key="precision-test-scene",
            now=datetime(2026, 8, 1, tzinfo=UTC),
        )
        job.status = JobStatus.SUCCEEDED
        job.result = {
            "method_version": METHOD_VERSION,
            "field_id": "F-03",
            "season": 2026,
            "boundary_revision": 0,
            "geometry_fingerprint": fingerprint,
            "observation": {
                "date": "2026-07-10",
                "acquired_at": "2026-07-10T10:00:00Z",
                "scene_id": "S2-TEST",
                "mean_ndvi": 0.5,
                "p10_ndvi": 0.3,
                "p90_ndvi": 0.7,
                "valid_percent": 80,
                "pixel_count": 100,
                "catalog_url": "https://example.invalid/synthetic",
            },
            "heterogeneity": {
                "date": "2026-07-10",
                "median_ndvi": 0.5,
                "spread_ndvi": 0.2,
                "low_area_percent": 20,
                "valid_percent": 80,
                "low_threshold": 0.35,
                "high_threshold": 0.65,
                "resolution_m": 20,
                "cells": [
                    {
                        "geometry": mapping(box(31.002, 48.502, 31.008, 48.508)),
                        "value": 0.3,
                        "zone": "low",
                    }
                ],
            },
        }
        session.commit()
    body = {
        key: value
        for key, value in trial_payload(http).items()
        if key
        not in {
            "treatments",
            "replicates",
            "strip_width_m",
            "heading_degrees",
            "headland_m",
            "seed",
        }
    }
    body.update(observation_date="2026-07-10", scene_id="S2-TEST", rates={"low": 75})
    response = http.post("/api/fields/F-03/precision/prescriptions", json=body)
    assert response.status_code == 201, response.text
    plan = response.json()
    features = plan["snapshot"]["features"]
    assert {item["properties"]["zone"] for item in features} == {"low", "fallback"}
    union = unary_union([shape(item["geometry"]) for item in features])
    assert union.symmetric_difference(shape(GEOMETRY)).area < 1e-11
    fallback = next(item for item in features if item["properties"]["zone"] == "fallback")
    assert len(shape(fallback["geometry"]).interiors) == 1
    approve(http, plan)
    assert http.get(f"/api/fields/F-03/precision/{plan['id']}/export/shapefile").status_code == 200
    bad = {**body, "client_id": str(uuid4()), "scene_id": "OTHER"}
    assert http.post("/api/fields/F-03/precision/prescriptions", json=bad).status_code == 409


def test_reprojection_repairs_self_touching_slivers_without_losing_area():
    # A valid UTM multipolygon from the 0.03 x 0.01 degree parity field at 48N.
    # Its narrow edge slivers touch at one point after WGS84 reprojection.
    utm = shape(
        {
            "type": "MultiPolygon",
            "coordinates": [
                [
                    [
                        [278461.6341666683, 5320569.080506225],
                        [277427.0388373061, 5320609.172758184],
                        [277715.7873503866, 5320597.9832836045],
                        [278461.6341666683, 5320569.080506225],
                    ]
                ],
                [
                    [
                        [277013.0741603497, 5321738.0912501905],
                        [276267.3695214694, 5321767.090058618],
                        [278504.4855748048, 5321680.384550424],
                        [277758.77951227804, 5321709.189414622],
                        [277013.0741603497, 5321738.0912501905],
                    ]
                ],
            ],
        }
    )
    projected = shape(transform_geom("EPSG:32631", "EPSG:4326", mapping(utm)))
    assert not projected.is_valid
    repaired = shape(geographic(utm, "EPSG:32631"))
    assert repaired.is_valid
    assert repaired.geom_type in {"Polygon", "MultiPolygon"}
    assert repaired.area == pytest.approx(projected.area, rel=1e-12)
