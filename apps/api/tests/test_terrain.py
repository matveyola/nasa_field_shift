from datetime import UTC, datetime

import numpy as np
import pytest
from rasterio.transform import from_origin
from rasterio.warp import transform_geom

from field_shift.modules.fields.geometry import contains_point
from field_shift.modules.fields.schemas import Polygon
from field_shift.modules.terrain import dem, service

NOW = datetime(2026, 11, 10, tzinfo=UTC)


def grid_field(z, epsg=32636):
    height, width = z.shape
    affine = from_origin(300000, 5550000, 30, 30)
    ring = [
        affine * (0, 0),
        affine * (width, 0),
        affine * (width, height),
        affine * (0, height),
        affine * (0, 0),
    ]
    geometry = Polygon.model_validate(
        transform_geom(epsg, "EPSG:4326", {"type": "Polygon", "coordinates": [ring]})
    )
    return dem.TerrainGrid(z, affine, epsg, ("test-source",)), geometry


@pytest.mark.parametrize(("axis", "end_is_lower"), [(0, "north"), (1, "west")])
def test_plane_downhill_directions_use_projected_meter_spacing(axis, end_is_lower, monkeypatch):
    # +3 m elevation per 30 m pixel gives exactly 10% slope.
    z = 100.0 + np.indices((12, 12))[axis] * 3
    grid, geometry = grid_field(z)
    monkeypatch.setattr(service, "load_terrain_grid", lambda _: grid)
    result = service.run_terrain_analysis("UA-01", geometry, NOW, 4)
    assert result.status == "ready" and result.coverage_percent == 100
    assert result.boundary_revision == 4
    assert result.elevation_min_m == 100 and result.elevation_max_m == 133
    assert result.vectors
    for arrow in result.vectors:
        assert arrow.slope_percent == pytest.approx(10)
        if end_is_lower == "north":
            assert arrow.end[1] > arrow.start[1]
        else:
            assert arrow.end[0] < arrow.start[0]
        assert contains_point(geometry.coordinates[0], *arrow.start)
        assert contains_point(geometry.coordinates[0], *arrow.end)


def test_southern_hemisphere_uses_south_utm_and_negative_tile_floor():
    geometry = Polygon(
        type="Polygon",
        coordinates=[
            [
                (-70.8, -33.4),
                (-70.79, -33.4),
                (-70.79, -33.39),
                (-70.8, -33.4),
            ]
        ],
    )
    epsg, _, _ = dem.target_grid(geometry)
    assert epsg == 32719
    assert "S34_00_W071_00" in dem.tile_url(-71, -34)
    assert "N00_00_E000_00" in dem.tile_url(0, 0)
    with pytest.raises(ValueError):
        dem.tile_url(180, 0)


def test_flat_elevations_are_real_zero_values_but_have_no_directions(monkeypatch):
    grid, geometry = grid_field(np.zeros((10, 10)))
    monkeypatch.setattr(service, "load_terrain_grid", lambda _: grid)
    result = service.run_terrain_analysis("FLAT", geometry, NOW)
    assert result.status == "ready" and result.coverage_percent == 100
    assert result.elevation_min_m == result.elevation_max_m == 0
    assert result.vectors == []
    assert result.reason == "No sufficiently resolved downhill directions in this field."


def test_missing_pixels_are_never_elevation_zero_or_direction_sources(monkeypatch):
    z = 100.0 + np.indices((20, 20))[1] * 3
    z[8:12, 8:12] = np.nan
    grid, geometry = grid_field(z)
    monkeypatch.setattr(service, "load_terrain_grid", lambda _: grid)
    result = service.run_terrain_analysis("GAPS", geometry, NOW)
    assert result.status == "ready" and result.coverage_percent == 96
    assert result.elevation_min_m == 100
    assert result.vectors
    inverse = ~grid.transform
    from rasterio.warp import transform

    for arrow in result.vectors:
        x, y = transform("EPSG:4326", grid.epsg, [arrow.start[0]], [arrow.start[1]])
        column, row = inverse * (x[0], y[0])
        row, column = int(row), int(column)
        assert np.isfinite(z[row - 1 : row + 2, column - 1 : column + 2]).all()
    assert "NaN" not in result.model_dump_json()


def test_insufficient_coverage_and_small_fields_are_explicitly_unavailable(monkeypatch):
    z = np.full((10, 10), np.nan)
    z[0] = 120
    grid, geometry = grid_field(z)
    monkeypatch.setattr(service, "load_terrain_grid", lambda _: grid)
    result = service.run_terrain_analysis("MISSING", geometry, NOW)
    assert result.status == "unavailable" and result.coverage_percent == 10
    assert result.elevation_min_m is None and not result.vectors
    assert result.reason == "Less than 90% of the field has usable elevation data."

    from rasterio.warp import transform_geom

    tiny = Polygon.model_validate(
        transform_geom(
            grid.epsg,
            "EPSG:4326",
            {
                "type": "Polygon",
                "coordinates": [
                    [
                        (300001, 5549999),
                        (300003, 5549999),
                        (300003, 5549997),
                        (300001, 5549997),
                        (300001, 5549999),
                    ]
                ],
            },
        )
    )
    result = service.run_terrain_analysis("TINY", tiny, NOW)
    assert result.status == "unavailable" and result.coverage_percent == 0
    assert result.reason == "The field is too small for the 30 m elevation grid."


def test_vector_response_is_bounded_and_ignores_external_field_pixels():
    grid, _ = grid_field(100.0 + np.indices((256, 256))[1] * 3)
    inside = np.zeros((256, 256), dtype=bool)
    inside[30:220, 30:220] = True
    vectors = service._vectors(grid, inside)
    assert 0 < len(vectors) <= service.MAX_VECTORS
    # No arrow can originate in the steep parts outside the explicitly selected field.
    assert len(service._vectors(grid, np.zeros_like(inside))) == 0


def test_oversized_or_out_of_projection_field_fails_before_network():
    for ring in (
        [(30, 50), (31, 50), (31, 51), (30, 51), (30, 50)],
        [(30, 85), (30.01, 85), (30.01, 85.01), (30, 85)],
        [(-179.9, 50), (179.9, 50), (179.9, 50.01), (-179.9, 50)],
    ):
        with pytest.raises(dem.TerrainCoverageError):
            dem.target_grid(Polygon(type="Polygon", coordinates=[ring]))


def test_public_coverage_failure_is_saved_but_transient_error_retries(monkeypatch):
    _, geometry = grid_field(np.zeros((12, 12)))

    def no_coverage(_):
        raise dem.TerrainCoverageError("No public elevation tile.")

    monkeypatch.setattr(service, "load_terrain_grid", no_coverage)
    result = service.run_terrain_analysis("NO_TILE", geometry, NOW)
    assert result.status == "unavailable" and result.reason == "No public elevation tile."

    def interrupted(_):
        raise dem.TerrainUnavailableError("The elevation source is temporarily unavailable.")

    monkeypatch.setattr(service, "load_terrain_grid", interrupted)
    with pytest.raises(dem.TerrainUnavailableError):
        service.run_terrain_analysis("RETRY", geometry, NOW)


def test_reader_only_requests_field_window(monkeypatch):
    class Dataset:
        crs = dem.rasterio.crs.CRS.from_epsg(4326)
        transform = from_origin(30, 51, 1 / 3600, 1 / 3600)
        width = height = 3600
        count = 1

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, band, *, window, masked):
            assert band == 1 and masked
            assert window.width < 100 and window.height < 100
            return np.ma.array(np.full((int(window.height), int(window.width)), 150.0))

        def window_transform(self, window):
            return dem.rasterio.windows.transform(window, self.transform)

    monkeypatch.setattr(dem.rasterio, "open", lambda _: Dataset())
    values, _ = dem._read_tile(dem.tile_url(30, 50), (30.5, 50.5, 30.51, 50.51))
    assert values.size < 10000 and (values == 150).all()


def test_saved_terrain_is_reused_only_for_current_boundary_and_method(monkeypatch):
    from field_shift.db import build_session_factory
    from field_shift.jobs import repository
    from field_shift.jobs.models import Job, JobKind
    from field_shift.models import metadata
    from field_shift.modules.fields.models import Field
    from field_shift.seed import seed_demo

    factory = build_session_factory("sqlite://")
    engine = factory.kw["bind"]
    metadata.create_all(engine)
    grid, geometry = grid_field(100.0 + np.indices((12, 12))[1] * 3)
    monkeypatch.setattr(service, "load_terrain_grid", lambda _: grid)
    result = service.run_terrain_analysis("F-01", geometry, NOW)
    try:
        with factory() as session:
            seed_demo(session)
            assert service.latest_terrain(session, "F-01") is None
            job = repository.submit(
                session,
                kind=JobKind.GEO_ML,
                field_id="F-01",
                inputs={"analysis": "terrain", "source": "historical", "season": 2026},
                boundary_revision=0,
                idempotency_key="terrain-unit",
                now=NOW,
            )
            job_id = job.id
            session.commit()
        with factory.begin() as session:
            lease = repository.claim(session, job_id, lease_seconds=135, now=NOW)
            assert lease is not None
            assert repository.succeed(session, lease, result.model_dump(mode="json"), now=NOW)
        with factory.begin() as session:
            assert service.latest_terrain(session, "F-01") == result
            assert service.latest_terrain(session, "F-02") is None
            field = session.get(Field, "F-01")
            field.boundary_revision = 1
        with factory.begin() as session:
            assert service.latest_terrain(session, "F-01") is None
            field = session.get(Field, "F-01")
            field.boundary_revision = 0
            job = session.get(Job, job_id)
            job.result = {**job.result, "model_version": "retired-model"}
        with factory() as session:
            assert service.latest_terrain(session, "F-01") is None
    finally:
        engine.dispose()


@pytest.fixture
def terrain_database(monkeypatch):
    from field_shift.db import build_session_factory
    from field_shift.jobs import repository
    from field_shift.models import metadata
    from field_shift.seed import seed_demo

    factory = build_session_factory("sqlite://")
    engine = factory.kw["bind"]
    metadata.create_all(engine)
    with factory() as session:
        seed_demo(session)
    monkeypatch.setattr(repository, "_database_time", lambda _session, now: now or NOW)

    def load(geometry):
        epsg, affine, shape = dem.target_grid(geometry)
        return dem.TerrainGrid(100.0 + np.indices(shape)[1] * 3, affine, epsg, ())

    monkeypatch.setattr(service, "load_terrain_grid", load)
    try:
        yield factory
    finally:
        engine.dispose()


def test_refresh_deduplicates_pending_and_complete_jobs_and_retries_failed(terrain_database):
    from fastapi.testclient import TestClient

    from field_shift.api import create_app
    from field_shift.jobs import repository
    from field_shift.jobs.worker import process_job
    from field_shift.modules.fields.models import Field

    with TestClient(create_app(session_factory=terrain_database)) as client:
        endpoint = "/api/fields/F-01/terrain"
        assert client.get(endpoint).json() is None
        first = client.post(endpoint + "/refresh")
        assert first.status_code == 202
        job_id = first.json()["id"]
        assert first.json()["kind"] == "geo_ml" and first.json()["analysis"] == "terrain"
        assert client.post(endpoint + "/refresh").json()["id"] == job_id
        process_job(terrain_database, job_id, lease_seconds=135, now=NOW)
        saved = client.get(endpoint)
        assert saved.status_code == 200 and saved.json()["status"] == "ready"
        assert saved.json()["vectors"]
        assert client.post(endpoint + "/refresh").json()["id"] == job_id
        # Another field's refresh and a later revision cannot reuse this result.
        assert client.post("/api/fields/F-02/terrain/refresh").json()["id"] != job_id
        with terrain_database.begin() as session:
            field = session.get(Field, "F-01")
            field.boundary_revision = 1
        assert client.get(endpoint).json() is None
        next_job = client.post(endpoint + "/refresh").json()
        assert next_job["id"] != job_id and next_job["boundary_revision"] == 1
        with terrain_database.begin() as session:
            lease = repository.claim(session, next_job["id"], lease_seconds=135, now=NOW)
            assert repository.fail(session, lease, "Failed after retries", retryable=False, now=NOW)
        retry = client.post(endpoint + "/refresh").json()
        assert retry["id"] not in (job_id, next_job["id"])
        assert client.post(endpoint + "/refresh").json()["id"] == retry["id"]
        assert client.get("/api/fields/unknown/terrain").status_code == 404
        assert client.post("/api/fields/unknown/terrain/refresh").status_code == 404


def test_transient_worker_failure_keeps_durable_retry_and_no_partial_result(
    terrain_database, monkeypatch
):
    from fastapi.testclient import TestClient

    from field_shift.api import create_app
    from field_shift.jobs.worker import process_job

    def unavailable(_):
        raise dem.TerrainUnavailableError("The elevation source is temporarily unavailable.")

    monkeypatch.setattr(service, "load_terrain_grid", unavailable)
    with TestClient(create_app(session_factory=terrain_database)) as client:
        endpoint = "/api/fields/F-01/terrain"
        job_id = client.post(endpoint + "/refresh").json()["id"]
        process_job(terrain_database, job_id, lease_seconds=135, now=NOW)
        saved = client.get(f"/api/jobs/{job_id}").json()
        assert saved["status"] == "retrying" and saved["attempts"] == 1
        assert saved["result"] is None
        assert saved["error"] == "The elevation source is temporarily unavailable."
        assert client.get(endpoint).json() is None
        assert client.post(endpoint + "/refresh").json()["id"] == job_id


@pytest.mark.parametrize(
    "override",
    [
        {"source": "demo"},
        {"source": "forecast"},
        {"irrigation_mm": 1},
        {"scenario": "dry_year"},
        {"sample_date": "2026-01-01"},
        {"latest": True},
        {"start_date": "2026-01-01", "end_date": "2026-01-02"},
    ],
)
def test_public_terrain_job_rejects_unrelated_inputs(override):
    from pydantic import ValidationError

    from field_shift.jobs.schemas import JobSubmission

    with pytest.raises(ValidationError):
        JobSubmission.model_validate(
            {
                "kind": "geo_ml",
                "field_id": "F-01",
                "inputs": {
                    "season": 2026,
                    "source": "historical",
                    "analysis": "terrain",
                    "scenario": "baseline",
                    **override,
                },
            }
        )
