from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pytest
from fastapi.testclient import TestClient
from field_shift_science.fields.schemas import Polygon
from pydantic import ValidationError
from rasterio.transform import from_origin
from shapely.geometry import box, shape
from sqlalchemy import select

from field_shift.api import create_app
from field_shift.db import build_session_factory
from field_shift.jobs import repository
from field_shift.jobs.models import Job, JobKind, JobStatus
from field_shift.jobs.schemas import JobSubmission
from field_shift.jobs.worker import process_job
from field_shift.models import metadata
from field_shift.modules.fields.models import Field, FieldPlanting
from field_shift.modules.imagery import vegetation, vegetation_catalog, vegetation_raster
from field_shift.modules.imagery.vegetation_catalog import VegetationUnavailableError
from field_shift.modules.imagery.vegetation_schemas import (
    Heterogeneity,
    VegetationCell,
    VegetationMonthResult,
    VegetationObservation,
)
from field_shift.seed import seed_demo

NOW = datetime(2026, 8, 15, tzinfo=UTC)
BBOX = (-93.42, 41.70, -93.41, 41.71)
GEOMETRY = Polygon(
    type="Polygon",
    coordinates=[
        [
            (-93.42, 41.70),
            (-93.41, 41.70),
            (-93.41, 41.71),
            (-93.42, 41.71),
            (-93.42, 41.70),
        ]
    ],
)


@pytest.fixture
def factory():
    sessions = build_session_factory("sqlite://")
    metadata.create_all(sessions.kw["bind"])
    with sessions() as session:
        seed_demo(session)
    try:
        yield sessions
    finally:
        sessions.kw["bind"].dispose()


def item(identifier="S2A_15TVG_20260710_0_L2A", cloud=5):
    return {
        "id": identifier,
        "collection": "sentinel-2-l2a",
        "properties": {
            "datetime": "2026-07-10T17:00:00Z",
            "proj:epsg": 32615,
            "eo:cloud_cover": cloud,
            "s2:processing_baseline": "05.11",
            "earthsearch:boa_offset_applied": True,
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[-94, 41], [-93, 41], [-93, 42], [-94, 42], [-94, 41]],
            ],
        },
        "assets": {
            key: {
                "href": "https://sentinel-cogs.s3.us-west-2.amazonaws.com/scenes/"
                + identifier
                + "/"
                + name,
                "raster:bands": [{"scale": 0.0001, "offset": -0.1}],
            }
            for key, name in (("red", "B04.tif"), ("nir", "B08.tif"), ("scl", "SCL.tif"))
        },
    }


def candidate(raw=None):
    return vegetation_catalog._candidate(
        raw if raw is not None else item(), BBOX, date(2026, 7, 1), date(2026, 7, 31)
    )


def test_harmonized_cog_does_not_apply_the_stale_asset_offset_twice():
    scene = candidate()
    assert scene is not None
    inside = np.zeros((6, 6), dtype=bool)
    inside[1:-1, 1:-1] = True
    values, valid = vegetation_raster.ndvi_pixels(
        np.full((6, 6), 1000),
        np.full((6, 6), 4000),
        np.full((6, 6), 4),
        inside,
        scene.red_scale,
        scene.nir_scale,
    )
    assert valid.sum() == 16
    np.testing.assert_allclose(values[valid], 0.6)


def test_ambiguous_modern_radiometry_is_excluded_not_guessed():
    raw = item()
    raw["properties"]["earthsearch:boa_offset_applied"] = False
    assert candidate(raw) is None
    raw["properties"]["s2:processing_baseline"] = "03.01"
    assert candidate(raw) is not None
    raw["properties"].pop("s2:processing_baseline")
    assert candidate(raw) is None


def test_msavi_uses_scaled_reflectance_and_the_shared_quality_mask():
    valid = np.array([[True, False], [True, True]])
    red = np.array([[1000, 1000], [4000, 2000]])
    nir = np.array([[4000, 4000], [1000, 2000]])
    values = vegetation_raster.msavi_pixels(red, nir, valid, 0.0001, 0.0001)
    np.testing.assert_allclose(values[valid], [0.44174243, -0.37979590, 0], atol=1e-8)
    assert np.isnan(values[0, 1])


@pytest.mark.parametrize(
    "change",
    [
        lambda raw: raw["assets"]["red"].update(href="http://127.0.0.1/secret.tif"),
        lambda raw: raw["assets"]["nir"].update(
            href="https://sentinel-cogs.s3.us-west-2.amazonaws.com.evil.test/B08.tif"
        ),
        lambda raw: raw["assets"]["scl"].update(
            href="https://sentinel-cogs.s3.us-west-2.amazonaws.com/a/SCL.tif?token=secret"
        ),
        lambda raw: raw["properties"].update({"eo:cloud_cover": float("nan")}),
        lambda raw: raw["properties"].update({"datetime": "2026-08-01T12:00:00Z"}),
        lambda raw: raw["properties"].update({"datetime": "2026-07-01T12:00:00"}),
        lambda raw: raw.update(id="../../metadata"),
        lambda raw: raw.update(collection="wrong-collection"),
    ],
)
def test_unsafe_or_out_of_range_catalog_item_is_not_read(change):
    raw = item()
    change(raw)
    assert candidate(raw) is None


def test_cloud_shadow_snow_nodata_and_unclassified_have_no_fake_ndvi():
    inside = np.ones((11, 11), dtype=bool)
    red = np.full((11, 11), 1000)
    nir = np.full((11, 11), 4000)
    for bad_class in (0, 1, 2, 3, 7, 8, 9, 10, 11):
        scl = np.full((11, 11), 4)
        scl[5, 5] = bad_class
        values, valid = vegetation_raster.ndvi_pixels(red, nir, scl, inside, 0.0001, 0.0001)
        assert not valid[4:7, 4:7].any()
        assert np.isnan(values[4:7, 4:7]).all()
        assert valid[2, 2]
    red[2, 2] = 0
    _, valid = vegetation_raster.ndvi_pixels(red, nir, np.full((11, 11), 4), inside, 0.0001, 0.0001)
    assert not valid[2, 2]


def test_clear_water_and_bare_soil_remain_observations_and_polygon_mask_is_respected():
    inside = np.zeros((6, 6), dtype=bool)
    inside[2:4, 2:4] = True
    for clear_class in (4, 5, 6):
        values, valid = vegetation_raster.ndvi_pixels(
            np.full((6, 6), 3000),
            np.full((6, 6), 1000),
            np.full((6, 6), clear_class),
            inside,
            0.0001,
            0.0001,
        )
        assert valid.sum() == 4
        np.testing.assert_allclose(values[valid], -0.5)
        assert np.isnan(values[~inside]).all()


def test_cells_are_clipped_to_field_and_map_payload_is_bounded():
    values = np.full((80, 80), 0.7)
    valid = np.ones_like(values, dtype=bool)
    transform = from_origin(500000, 4650000, 20, 20)
    footprint = box(500007, 4648411, 501591, 4649993)
    cells, resolution = vegetation_raster._cells(
        values, valid, footprint, transform, 32615, 0.55, 0.85
    )
    assert resolution == 40
    assert 0 < len(cells) <= vegetation_raster.MAX_MAP_CELLS
    for cell in cells:
        projected = vegetation_raster.transform_geom(
            "EPSG:4326", "EPSG:32615", cell.geometry.model_dump()
        )
        assert footprint.buffer(0.002).covers(shape(projected))
        assert cell.zone == "typical" and cell.value == 0.7


def test_coarse_map_blocks_do_not_paint_over_cloud_or_nodata_pixels():
    values = np.full((52, 52), 0.7)
    valid = np.ones_like(values, dtype=bool)
    valid[0:2, 0:2] = False
    valid[0, 0] = True
    values[~valid] = np.nan
    footprint = box(500000, 4648960, 501040, 4650000)
    cells, resolution = vegetation_raster._cells(
        values, valid, footprint, from_origin(500000, 4650000, 20, 20), 32615, 0.55, 0.85
    )
    assert resolution == 40
    for cell in cells:
        projected = vegetation_raster.transform_geom(
            "EPSG:4326", "EPSG:32615", cell.geometry.model_dump()
        )
        assert shape(projected).intersection(box(500000, 4649960, 500040, 4650000)).area < 0.1


def test_oversized_and_too_small_footprints_are_rejected_before_raster_read():
    with pytest.raises(ValueError, match="65,536"):
        vegetation_raster.grid_for(
            Polygon(
                type="Polygon",
                coordinates=[
                    [
                        (-93.5, 41.7),
                        (-93.3, 41.7),
                        (-93.3, 41.9),
                        (-93.5, 41.9),
                        (-93.5, 41.7),
                    ]
                ],
            ),
            32615,
        )
    with pytest.raises(ValueError, match="ten"):
        vegetation_raster.grid_for(
            Polygon(
                type="Polygon",
                coordinates=[
                    [
                        (-93.42, 41.7),
                        (-93.41999, 41.7),
                        (-93.41999, 41.70001),
                        (-93.42, 41.70001),
                        (-93.42, 41.7),
                    ]
                ],
            ),
            32615,
        )


def test_catalog_limits_response_candidates_dates_and_duplicate_acquisitions(monkeypatch):
    import json

    captured = []
    scenes = []
    for day, cloud in ((5, 0), (10, 1), (10, 1), (15, 2), (20, 3)):
        raw = item("S2A_15TVG_202607" + str(day) + "_" + str(len(scenes)), cloud)
        raw["properties"]["datetime"] = f"2026-07-{day:02}T17:00:00Z"
        scenes.append(raw)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, count):
            assert count == vegetation_catalog.MAX_CATALOG_BYTES + 1
            return json.dumps({"features": scenes}).encode()

    def open_(request, timeout):
        captured.append(parse_qs(urlsplit(request.full_url).query))
        assert timeout == 8
        return Response()

    monkeypatch.setattr(vegetation_catalog, "urlopen", open_)
    selected = vegetation_catalog.search_month(GEOMETRY, date(2026, 7, 1), date(2026, 7, 31))
    assert [scene.acquired_at.day for scene in selected] == [5, 10, 15]
    assert captured[0]["limit"] == ["100"]
    assert captured[0]["datetime"] == ["2026-07-01T00:00:00Z/2026-07-31T23:59:59.999Z"]


def test_no_clear_scene_is_distinct_from_upstream_failure(monkeypatch):
    monkeypatch.setattr(vegetation, "search_month", lambda *_args: [candidate()])
    monkeypatch.setattr(vegetation, "analyse_scene", lambda *_args: None)
    result = vegetation.run_vegetation_month(
        "F-01", 2026, date(2026, 7, 31), GEOMETRY, "mask", NOW, 0
    )
    assert result.status == "no_clear_scene"
    assert result.observation is None and result.heterogeneity is None

    def failure(*_args):
        raise VegetationUnavailableError("upstream timeout")

    monkeypatch.setattr(vegetation, "analyse_scene", failure)
    with pytest.raises(VegetationUnavailableError):
        vegetation.run_vegetation_month("F-01", 2026, date(2026, 7, 31), GEOMETRY, "mask", NOW, 0)


def test_invalid_periods_are_rejected_before_network(monkeypatch):
    def unexpected(*_args):
        pytest.fail("Invalid periods must not reach the provider")

    monkeypatch.setattr(vegetation, "search_month", unexpected)
    for season, cutoff in (
        (2016, date(2016, 7, 31)),
        (2026, date(2027, 1, 1)),
        (2026, date(2026, 8, 31)),
    ):
        with pytest.raises(ValueError):
            vegetation.run_vegetation_month("F-01", season, cutoff, GEOMETRY, "mask", NOW, 0)


def test_job_contract_rejects_mixed_analyses_and_accepts_monthly_vegetation():
    payload = {
        "kind": "geo_ml",
        "field_id": "F-01",
        "inputs": {
            "season": 2025,
            "scenario": "baseline",
            "source": "historical",
            "analysis": "vegetation",
            "sample_date": "2025-07-31",
        },
    }
    assert JobSubmission.model_validate(payload).inputs.analysis == "vegetation"
    for patch in (
        {"latest": True},
        {"irrigation_mm": 1},
        {"source": "demo"},
        {"analysis": "terrain"},
        {"sample_date": None},
        {"sample_date": "2024-07-31"},
        {"scenario": "dry_year"},
    ):
        invalid = deepcopy(payload)
        invalid["inputs"].update(patch)
        with pytest.raises(ValidationError):
            JobSubmission.model_validate(invalid)


def test_refresh_is_idempotent_durable_and_does_not_redownload_closed_months(factory):
    with factory.begin() as session:
        first = vegetation.queue_vegetation_refresh(session, "F-01", 2026, now=NOW)
        assert len(first) == 8
        assert [job.inputs["sample_date"] for job in first] == sorted(
            [job.inputs["sample_date"] for job in first], reverse=True
        )
        identifiers = [job.id for job in first]
    with factory.begin() as session:
        again = vegetation.queue_vegetation_refresh(session, "F-01", 2026, now=NOW)
        assert [job.id for job in again] == identifiers
        for job in again:
            job.status = JobStatus.SUCCEEDED
            job.result = {"method_version": "sentinel-ndvi-monthly-v1"}
    with factory.begin() as session:
        assert not vegetation.queue_vegetation_refresh(session, "F-01", 2026, now=NOW)
        next_day = vegetation.queue_vegetation_refresh(
            session, "F-01", 2026, now=NOW + timedelta(days=1)
        )
        assert len(next_day) == 1
        assert next_day[0].inputs["sample_date"] == "2026-08-16"


def _ready_month(field_id, season, cutoff, _geometry, fingerprint, at, boundary_revision):
    return VegetationMonthResult(
        field_id=field_id,
        season=season,
        month=cutoff.month,
        cutoff=cutoff,
        geometry_fingerprint=fingerprint,
        boundary_revision=boundary_revision,
        generated_at=at,
        status="ready",
        observation=VegetationObservation(
            date=cutoff,
            acquired_at=datetime.combine(cutoff, datetime.min.time(), UTC),
            scene_id="S2-real-provider-fixture",
            mean_ndvi=0.7,
            p10_ndvi=0.5,
            p90_ndvi=0.8,
            valid_percent=90,
            pixel_count=100,
            catalog_url="https://example.org/test-scene",
        ),
    )


def test_worker_persists_result_and_planting_change_invalidates_without_boundary_revision(
    factory,
    monkeypatch,
):
    monkeypatch.setattr(vegetation, "run_vegetation_month", _ready_month)
    with factory.begin() as session:
        jobs = vegetation.queue_vegetation_refresh(session, "F-01", 2026, now=NOW)
        first_id, second_id = jobs[0].id, jobs[1].id
    process_job(factory, first_id, lease_seconds=120, now=NOW)
    with factory.begin() as session:
        job = session.get(Job, first_id)
        assert job.status == "succeeded" and job.attempts == 1
        result = vegetation.vegetation_season(session, "F-01", 2026, now=NOW)
        assert len(result.observations) == 1 and result.status == "partial"
        assert result.months[-1].status == "ready"
        assert result.months[-2].status == "pending"
        original_hash = result.geometry_fingerprint
        field = session.get(Field, "F-01")
        cropped = deepcopy(field.geometry)
        cropped["coordinates"][0] = [
            [-93.4195, 41.7063],
            [-93.4185, 41.7063],
            [-93.4185, 41.7053],
            [-93.4195, 41.7053],
            [-93.4195, 41.7063],
        ]
        session.add(FieldPlanting(field_id="F-01", season=2026, geometry=cropped, area_ha=1))
    with factory() as session:
        result = vegetation.vegetation_season(session, "F-01", 2026, now=NOW)
        assert result.geometry_fingerprint != original_hash
        assert result.observations == [] and result.status == "unavailable"
        assert result.boundary_revision == 0
    process_job(factory, second_id, lease_seconds=120, now=NOW)
    with factory() as session:
        rejected = session.get(Job, second_id)
        assert rejected.status == "failed"
        assert "planted area changed" in rejected.error


def test_upstream_failure_retries_and_remains_distinct_from_missing_cloud_data(
    factory, monkeypatch
):
    def failure(*_args):
        raise VegetationUnavailableError("Sentinel-2 vegetation data is temporarily unavailable.")

    monkeypatch.setattr(vegetation, "run_vegetation_month", failure)
    with factory.begin() as session:
        job_id = vegetation.queue_vegetation_refresh(session, "F-01", 2026, now=NOW)[0].id
    process_job(factory, job_id, lease_seconds=120, now=NOW)
    with factory() as session:
        job = session.get(Job, job_id)
        assert job.status == "retrying" and job.result is None
        result = vegetation.vegetation_season(session, "F-01", 2026, now=NOW)
        assert not result.observations and result.months[0].status == "pending"


def test_read_endpoint_has_no_provider_work_and_refresh_is_explicit(factory, monkeypatch):
    def unexpected(*_args):
        pytest.fail("HTTP reads and submissions must not download satellite imagery")

    monkeypatch.setattr(vegetation, "search_month", unexpected)
    with TestClient(create_app(session_factory=factory)) as client:
        result = client.get("/api/fields/F-01/vegetation?season=2025")
        assert result.status_code == 200
        assert result.json()["observations"] == []
        assert result.json()["status"] == "unavailable"
        refreshed = client.post("/api/fields/F-01/vegetation/refresh?season=2025")
        assert refreshed.status_code == 202
        assert len(refreshed.json()["jobs"]) == 12
        assert all(job["analysis"] == "vegetation" for job in refreshed.json()["jobs"])
        assert client.get("/api/fields/unknown/vegetation?season=2025").status_code == 404
        assert client.get("/api/fields/F-01/vegetation?season=2100").status_code == 422


def test_saved_scene_read_matches_acquisition_date_scene_and_footprint(factory, monkeypatch):
    def unexpected(*_args):
        pytest.fail("Reading a saved scene must not download imagery")

    monkeypatch.setattr(vegetation, "search_month", unexpected)
    with factory.begin() as session:
        jobs = vegetation.queue_vegetation_refresh(session, "F-01", 2025, now=NOW, max_new_jobs=2)
        for job, value in zip(jobs, (0.65, 0.75), strict=True):
            cutoff = date.fromisoformat(job.inputs["sample_date"])
            day = cutoff.replace(day=10)
            snapshot = _ready_month(
                "F-01", 2025, cutoff, GEOMETRY, job.inputs["vegetation_mask_hash"], NOW, 0
            )
            snapshot.observation.date = day
            snapshot.observation.acquired_at = datetime.combine(day, datetime.min.time(), UTC)
            snapshot.observation.scene_id = f"sentinel-{day}"
            snapshot.heterogeneity = Heterogeneity(
                date=day,
                median_ndvi=value,
                spread_ndvi=0.1,
                low_area_percent=0,
                valid_percent=90,
                low_threshold=value - 0.15,
                high_threshold=value + 0.15,
                resolution_m=20,
                cells=[VegetationCell(geometry=GEOMETRY, value=value, zone="typical")],
            )
            job.status = JobStatus.SUCCEEDED
            job.result = snapshot.model_dump(mode="json")

    with TestClient(create_app(session_factory=factory)) as client:
        endpoint = "/api/fields/F-01/vegetation/scene"
        params = {
            "season": 2025,
            "observation_date": "2025-11-10",
            "scene_id": "sentinel-2025-11-10",
        }
        response = client.get(endpoint, params=params)
        assert response.status_code == 200
        scene = response.json()
        assert scene["observation"]["date"] == "2025-11-10"
        assert scene["heterogeneity"]["cells"][0]["value"] == 0.75
        assert client.get(endpoint, params=params | {"scene_id": "other"}).json() is None
        cutoff = client.get(endpoint, params=params | {"observation_date": "2025-11-30"})
        assert cutoff.json() is None
        wrong_season = client.get(endpoint, params=params | {"observation_date": "2024-11-10"})
        assert wrong_season.status_code == 422
        assert client.get(endpoint.replace("F-01", "F-02"), params=params).json() is None
        with factory.begin() as session:
            session.add(
                FieldPlanting(
                    field_id="F-01",
                    season=2025,
                    geometry=GEOMETRY.model_dump(mode="json"),
                    area_ha=1,
                )
            )
        assert client.get(endpoint, params=params).json() is None


def test_scheduler_is_opt_in_and_refresh_submission_has_a_hard_budget(factory):
    assert vegetation.enqueue_vegetation_refresh(factory, now=NOW) == 0
    with factory.begin() as session:
        jobs = vegetation.queue_vegetation_refresh(session, "F-01", 2026, now=NOW, max_new_jobs=2)
        assert len(jobs) == 2
    # Existing active jobs are reused; only remaining missing months are new.
    assert vegetation.enqueue_vegetation_refresh(factory, now=NOW + timedelta(hours=1)) == 6


def test_new_geo_analyses_never_masquerade_as_surface_moisture(factory):
    from field_shift.modules.geo_ml.service import latest_surface_moisture

    with factory.begin() as session:
        jobs = vegetation.queue_vegetation_refresh(session, "F-01", 2026, now=NOW)
        for job in jobs:
            job.status = JobStatus.SUCCEEDED
            job.result = {"analysis": "vegetation", "season": 2026}
        terrain = repository.submit(
            session,
            kind=JobKind.GEO_ML,
            field_id="F-01",
            now=NOW,
            inputs={"season": 2026, "source": "historical", "analysis": "terrain"},
        )
        terrain.status = JobStatus.SUCCEEDED
        terrain.result = {"analysis": "terrain"}
    with factory() as session:
        assert latest_surface_moisture(session, "F-01", None, "historical") is None
        assert latest_surface_moisture(session, "F-01", 2026, "historical") is None


def test_explicit_failed_retry_is_idempotent_while_scheduler_respects_cooldown(factory):
    with factory.begin() as session:
        jobs = vegetation.queue_vegetation_refresh(session, "F-01", 2026, now=NOW)
        failed_id = jobs[0].id
        jobs[0].status = JobStatus.FAILED
        jobs[0].error = "Temporary source failure"
    with factory.begin() as session:
        scheduled = vegetation.queue_vegetation_refresh(session, "F-01", 2026, now=NOW)
        assert failed_id not in [job.id for job in scheduled]
        assert len(scheduled) == 7
        manual = vegetation.queue_vegetation_refresh(
            session, "F-01", 2026, now=NOW, retry_failed=True
        )
        retry_id = manual[0].id
        assert retry_id != failed_id and len(manual) == 8
    with factory.begin() as session:
        repeated = vegetation.queue_vegetation_refresh(
            session, "F-01", 2026, now=NOW, retry_failed=True
        )
        assert repeated[0].id == retry_id


def test_explicit_refresh_upgrades_extra_bands_without_background_archive_download(factory):
    with factory.begin() as session:
        first = vegetation.queue_vegetation_refresh(session, "F-01", 2025, now=NOW, only_month=7)
        old_id = first[0].id
        first[0].status = JobStatus.SUCCEEDED
        first[0].result = {"method_version": "sentinel-ndvi-monthly-v1"}
    with factory.begin() as session:
        assert (
            vegetation.queue_vegetation_refresh(session, "F-01", 2025, now=NOW, only_month=7) == []
        )
        upgrade = vegetation.queue_vegetation_refresh(
            session, "F-01", 2025, now=NOW, only_month=7, retry_failed=True
        )
        assert len(upgrade) == 1 and upgrade[0].id != old_id
        upgrade_id = upgrade[0].id
    with factory.begin() as session:
        repeated = vegetation.queue_vegetation_refresh(
            session, "F-01", 2025, now=NOW, only_month=7, retry_failed=True
        )
        assert len(repeated) == 1 and repeated[0].id == upgrade_id


def test_vegetation_summary_omits_map_cells_without_mutating_saved_result(factory, monkeypatch):
    monkeypatch.setattr(vegetation, "run_vegetation_month", _ready_month)
    with factory.begin() as session:
        job_id = vegetation.queue_vegetation_refresh(session, "F-01", 2026, now=NOW)[0].id
    process_job(factory, job_id, lease_seconds=120, now=NOW)
    with factory.begin() as session:
        job = session.get(Job, job_id)
        payload = deepcopy(job.result)
        payload["heterogeneity"] = {
            "date": "2026-08-15",
            "median_ndvi": 0.7,
            "spread_ndvi": 0.3,
            "low_area_percent": 5,
            "valid_percent": 90,
            "low_threshold": 0.55,
            "high_threshold": 0.85,
            "resolution_m": 20,
            "cells": [
                {"geometry": GEOMETRY.model_dump(mode="json"), "value": 0.7, "zone": "typical"}
            ],
        }
        job.result = payload
    with factory() as session:
        full = vegetation.vegetation_season(session, "F-01", 2026, now=NOW)
        summary = vegetation.vegetation_season(session, "F-01", 2026, now=NOW, include_cells=False)
        assert full.observations == summary.observations
        assert full.heterogeneity.low_area_percent == summary.heterogeneity.low_area_percent == 5
        assert len(full.heterogeneity.cells) == 1 and summary.heterogeneity.cells == []
        assert len(session.get(Job, job_id).result["heterogeneity"]["cells"]) == 1


def test_month_rollover_finishes_partial_catalog_then_rechecks_after_publication_grace(factory):
    with factory.begin() as session:
        jobs = vegetation.queue_vegetation_refresh(session, "F-01", 2026, now=NOW)
        for job in jobs:
            job.status = JobStatus.SUCCEEDED
            job.result = {"method_version": "sentinel-ndvi-monthly-v1"}
    september = datetime(2026, 9, 1, tzinfo=UTC)
    with factory.begin() as session:
        catchup = vegetation.queue_vegetation_refresh(session, "F-01", 2026, now=september)
        assert [job.inputs["sample_date"] for job in catchup] == ["2026-09-01", "2026-08-31"]
        for job in catchup:
            job.status = JobStatus.SUCCEEDED
            job.result = {"method_version": "sentinel-ndvi-monthly-v1"}
    with factory.begin() as session:
        before_grace = vegetation.queue_vegetation_refresh(
            session, "F-01", 2026, now=september + timedelta(days=1)
        )
        assert [job.inputs["sample_date"] for job in before_grace] == ["2026-09-02"]
        before_grace[0].status = JobStatus.SUCCEEDED
        before_grace[0].result = {"method_version": "sentinel-ndvi-monthly-v1"}
    with factory.begin() as session:
        after_grace = vegetation.queue_vegetation_refresh(
            session, "F-01", 2026, now=september + timedelta(days=6)
        )
        assert [job.inputs["sample_date"] for job in after_grace] == ["2026-09-07", "2026-08-31"]
        for job in after_grace:
            job.status = JobStatus.SUCCEEDED
            job.result = {"method_version": "sentinel-ndvi-monthly-v1"}
    with factory.begin() as session:
        frozen = vegetation.queue_vegetation_refresh(
            session, "F-01", 2026, now=september + timedelta(days=7)
        )
        assert [job.inputs["sample_date"] for job in frozen] == ["2026-09-08"]


def test_new_year_finalizes_only_subscribed_december_and_is_idempotent(factory):
    december = datetime(2026, 12, 15, tzinfo=UTC)
    january = datetime(2027, 1, 1, tzinfo=UTC)
    with factory.begin() as session:
        jobs = vegetation.queue_vegetation_refresh(session, "F-01", 2026, now=december)
        for job in jobs:
            job.status = JobStatus.SUCCEEDED
            job.result = {"method_version": "sentinel-ndvi-monthly-v1"}
    assert vegetation.enqueue_vegetation_refresh(factory, now=january) == 1
    with factory() as session:
        jobs = list(session.scalars(select(Job).where(Job.field_id == "F-01")))
        initial_ids = {job.id for job in jobs}
        pending = [job for job in jobs if job.status == JobStatus.QUEUED]
        assert len(pending) == 1
        assert pending[0].inputs["sample_date"] == "2026-12-31"
        assert all(job.inputs["season"] == 2026 for job in jobs)
        finalizing_id = pending[0].id
    vegetation.enqueue_vegetation_refresh(factory, now=january + timedelta(minutes=1))
    with factory.begin() as session:
        assert set(session.scalars(select(Job.id).where(Job.field_id == "F-01"))) == initial_ids
        job = session.get(Job, finalizing_id)
        job.status = JobStatus.SUCCEEDED
        job.updated_at = january
        job.result = {"method_version": "sentinel-ndvi-monthly-v1"}
    assert vegetation.enqueue_vegetation_refresh(factory, now=january + timedelta(days=1)) == 0
    assert vegetation.enqueue_vegetation_refresh(factory, now=january + timedelta(days=6)) == 1
    with factory.begin() as session:
        pending = list(session.scalars(select(Job).where(Job.status == JobStatus.QUEUED)))
        assert len(pending) == 1 and pending[0].inputs["sample_date"] == "2026-12-31"
        pending[0].status = JobStatus.SUCCEEDED
        pending[0].result = {"method_version": "sentinel-ndvi-monthly-v1"}
        pending[0].updated_at = january + timedelta(days=6)
    assert vegetation.enqueue_vegetation_refresh(factory, now=january + timedelta(days=7)) == 0


def test_year_finalization_catches_up_after_outage_without_rescanning_other_months(factory):
    with factory.begin() as session:
        jobs = vegetation.queue_vegetation_refresh(
            session,
            "F-01",
            2026,
            now=NOW,
            max_new_jobs=1,
        )
        jobs[0].status = JobStatus.SUCCEEDED
        jobs[0].result = {"method_version": "sentinel-ndvi-monthly-v1"}
    resumed = datetime(2027, 2, 20, tzinfo=UTC)
    assert vegetation.enqueue_vegetation_refresh(factory, now=resumed) == 1
    with factory() as session:
        jobs = list(session.scalars(select(Job).where(Job.field_id == "F-01")))
        assert sorted(job.inputs["sample_date"] for job in jobs) == ["2026-08-15", "2026-12-31"]
        assert not list(session.scalars(select(Job).where(Job.field_id == "F-02")))
