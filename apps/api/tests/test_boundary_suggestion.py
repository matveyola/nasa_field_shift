from datetime import UTC, date, datetime
from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.io import MemoryFile
from rasterio.warp import transform

from field_shift.api import create_app
from field_shift.modules.fields import boundary_suggestion as suggestion
from field_shift.modules.fields.geometry import area_hectares, contains_point
from field_shift.modules.imagery.sentinel import SatelliteImage

URL = "/api/fields/boundary-suggestion"
LOCATION = (22.35, 48.5)
MODEL_URL = "http://127.0.0.1:8091/predict"
NO_MODEL_CONTOUR = {
    "geometry": None,
    "source": "Delineate Anything v2",
    "imagery_date": None,
    "imagery_source": None,
    "failure_reason": "no_imagery",
}


def test_unconfigured_model_does_not_access_imagery(monkeypatch):
    monkeypatch.delenv("FIELD_SHIFT_BOUNDARY_MODEL_URL", raising=False)

    def unexpected_call(*_args):
        raise AssertionError("A disabled model must not fetch imagery")

    monkeypatch.setattr(suggestion, "satellite_image_at", unexpected_call)
    with TestClient(create_app()) as client:
        response = client.post(URL, json={"position": LOCATION})
        assert response.status_code == 200
        assert response.json() is None
        assert client.post(URL, json={"position": [LOCATION[0], 90]}).status_code == 422


def test_suggestion_keeps_a_valid_clicked_component(monkeypatch):
    size = suggestion.CHIP_SIZE
    center = suggestion.CHIP_CENTER
    epsg = 32634
    xs, ys = transform("EPSG:4326", f"EPSG:{epsg}", [LOCATION[0]], [LOCATION[1]])
    grid = suggestion.from_origin(xs[0] - center * 10 - 5, ys[0] + center * 10 + 5, 10, 10)
    scene = SatelliteImage(
        id="scene",
        acquired_at=datetime(2025, 9, 8, tzinfo=UTC),
        on_or_before=date(2025, 9, 14),
        tile_cloud_percent=0,
        visual_url="unused",
        catalog_url="unused",
        epsg=epsg,
        bounds=(22.32, 48.47, 22.38, 48.53),
    )
    cropped = np.zeros((size, size), dtype=np.uint8)
    cropped[center - 10 : center + 10, : center + 20] = 1
    viable = np.zeros_like(cropped)
    viable[center - 10 : center + 10, center - 20 : center + 20] = 1
    viable[:6, :6] = 1  # An unrelated, cropped component in the same mask.
    monkeypatch.setenv("FIELD_SHIFT_BOUNDARY_MODEL_URL", MODEL_URL)
    monkeypatch.setattr(suggestion, "satellite_image_at", lambda *_args: scene)
    monkeypatch.setattr(
        suggestion,
        "read_chip",
        lambda *_args: (np.full((size, size, 3), 32, np.uint8), np.ones_like(viable, bool), grid),
    )

    class Inference:
        def open(self, request, timeout):
            assert request.get_method() == "POST"
            assert len(request.data) == 256 * 256 * 3
            assert timeout == suggestion.MODEL_TIMEOUT_SECONDS
            masks = np.asarray([cropped, viable], dtype=np.uint8)
            response = BytesIO(np.packbits(masks).tobytes())
            response.headers = Message()
            response.headers["Content-Type"] = "application/octet-stream"
            return response

    monkeypatch.setattr(suggestion, "build_opener", lambda *_handlers: Inference())

    with TestClient(create_app()) as client:
        response = client.post(URL, json={"position": LOCATION})

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "Delineate Anything v2"
    assert body["imagery_date"] == "2025-09-08"
    ring = body["geometry"]["coordinates"][0]
    assert contains_point(ring, *LOCATION)
    assert 7.9 < area_hectares(ring) < 8.1


def test_configured_model_timeout_returns_no_model_contour(monkeypatch):
    monkeypatch.setenv("FIELD_SHIFT_BOUNDARY_MODEL_URL", MODEL_URL)

    def timeout(*_args):
        raise TimeoutError

    monkeypatch.setattr(suggestion, "satellite_image_at", timeout)
    monkeypatch.setattr(suggestion, "read_cloudless_chip", timeout)
    with TestClient(create_app()) as client:
        for _ in range(2):
            response = client.post(URL, json={"position": LOCATION})
            assert response.status_code == 200
            assert response.json() == NO_MODEL_CONTOUR


@pytest.mark.parametrize(
    "configuration", ["not-an-endpoint", "http://127.0.0.1:bad/predict", MODEL_URL]
)
def test_configured_model_without_imagery_returns_no_model_contour(monkeypatch, configuration):
    monkeypatch.setenv("FIELD_SHIFT_BOUNDARY_MODEL_URL", configuration)
    catalog_lookups = []
    monkeypatch.setattr(
        suggestion, "satellite_image_at", lambda *args: catalog_lookups.append(args)
    )
    monkeypatch.setattr(suggestion, "read_cloudless_chip", lambda *_args: None)
    with TestClient(create_app()) as client:
        response = client.post(URL, json={"position": LOCATION})
        assert response.status_code == 200
        expected = "no_imagery" if configuration == MODEL_URL else "model_unavailable"
        assert response.json() == {**NO_MODEL_CONTOUR, "failure_reason": expected}
    assert len(catalog_lookups) == (1 if configuration == MODEL_URL else 0)


def test_busy_model_returns_no_model_contour(monkeypatch):
    monkeypatch.setenv("FIELD_SHIFT_BOUNDARY_MODEL_URL", MODEL_URL)

    def unexpected_call(*_args):
        raise AssertionError("The busy model must not fetch imagery")

    monkeypatch.setattr(suggestion, "satellite_image_at", unexpected_call)
    with suggestion._slots, suggestion._slots, TestClient(create_app()) as client:
        response = client.post(URL, json={"position": LOCATION})
        assert response.status_code == 200
        assert response.json() == {**NO_MODEL_CONTOUR, "failure_reason": "busy"}


def test_read_chip_preserves_rgb_order_and_centers_the_click(tmp_path):
    longitude, latitude = LOCATION
    epsg = 32634
    xs, ys = transform("EPSG:4326", f"EPSG:{epsg}", [longitude], [latitude])
    radius = (suggestion.CHIP_CENTER + 0.5) * suggestion.RESOLUTION_M
    grid = suggestion.from_origin(xs[0] - radius, ys[0] + radius, 10, 10)
    path = tmp_path / "source.tif"
    size = suggestion.CHIP_SIZE
    data = np.zeros((3, size, size), dtype=np.uint8)
    data[0, 1:, :] = 43
    data[1, 1:, :] = 82
    data[2, 1:, :] = 123
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        count=3,
        dtype="uint8",
        width=size,
        height=size,
        transform=grid,
        crs=f"EPSG:{epsg}",
    ) as raster:
        raster.write(data)
    scene = SatelliteImage(
        id="local-scene",
        acquired_at=datetime(2025, 9, 8, tzinfo=UTC),
        on_or_before=date(2025, 9, 14),
        tile_cloud_percent=0,
        visual_url=str(path),
        catalog_url="unused",
        epsg=epsg,
        bounds=(22.32, 48.47, 22.38, 48.53),
    )
    rgb, valid, calculated = suggestion.read_chip(scene, longitude, latitude)
    assert calculated == grid
    assert not valid[0].any()
    assert valid[1:].all()
    np.testing.assert_array_equal(
        rgb[suggestion.CHIP_CENTER, suggestion.CHIP_CENTER], [43, 82, 123]
    )


def test_bondurant_model_input_uses_local_imagery_before_catalog(monkeypatch):
    image = Path(__file__).resolve().parents[3] / "apps/web/public/bondurant-usgs-aerial.webp"
    position = (-93.40905, 41.70109)
    monkeypatch.setenv("FIELD_SHIFT_BOUNDARY_MODEL_URL", MODEL_URL)
    monkeypatch.setenv("FIELD_SHIFT_BOUNDARY_DEMO_IMAGE", str(image))

    def unexpected_catalog(*_args):
        raise AssertionError("The bundled demo must work without a catalog request")

    model_inputs = []

    def masks(url, rgb):
        assert url == MODEL_URL
        assert rgb.shape == (256, 256, 3)
        assert rgb.max() > rgb.min()
        model_inputs.append(rgb)
        mask = np.zeros((256, 256), dtype=np.uint8)
        if len(model_inputs) == 1:
            mask[115:140, :145] = 1  # This first outline is cut off by the chip edge.
        else:
            mask[115:140, 112:145] = 1
        return [mask]

    monkeypatch.setattr(suggestion, "satellite_image_at", unexpected_catalog)
    monkeypatch.setattr(suggestion, "predict_masks", masks)
    response = TestClient(create_app()).post(URL, json={"position": position})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "Delineate Anything v2"
    assert body["imagery_date"] is None
    assert contains_point(body["geometry"]["coordinates"][0], *position)
    assert len(model_inputs) == 2
    assert not np.array_equal(*model_inputs)


def test_new_iowa_field_uses_usgs_aerial_imagery(monkeypatch):
    position = (-93.4561, 41.7269)
    monkeypatch.setenv("FIELD_SHIFT_BOUNDARY_MODEL_URL", MODEL_URL)
    size = suggestion.CHIP_SIZE
    size_parameter = f"{size},{size}"
    image = np.full((4, size, size), 255, dtype=np.uint8)
    image[:3] = np.array([30, 90, 40])[:, None, None]
    image[3, :, :20] = 0
    with MemoryFile() as file:
        with file.open(driver="PNG", width=size, height=size, count=4, dtype="uint8") as png:
            png.write(image)
        body = file.read()

    class Imagery:
        def open(self, request, timeout):
            parsed = urlsplit(request.full_url)
            assert parsed.netloc == "basemap.nationalmap.gov"
            params = parse_qs(parsed.query)
            assert params["bboxSR"] == params["imageSR"] == ["3857"]
            assert params["size"] == [size_parameter]
            response = BytesIO(body)
            response.headers = Message()
            response.headers["Content-Type"] = "image/png"
            return response

    def masks(_url, rgb):
        np.testing.assert_array_equal(rgb[size // 2, size // 2], [30, 90, 40])
        clipped = np.zeros((size, size), dtype=np.uint8)
        clipped[95:145, 20:150] = 1
        complete = np.zeros_like(clipped)
        complete[95:145, 90:150] = 1
        return [clipped, complete]

    monkeypatch.setattr(suggestion, "build_opener", lambda *_handlers: Imagery())
    monkeypatch.setattr(suggestion, "predict_masks", masks)
    response = TestClient(create_app()).post(URL, json={"position": position})
    assert response.status_code == 200
    ring = response.json()["geometry"]["coordinates"][0]
    assert contains_point(ring, *position)
    assert 11.5 < area_hectares(ring) < 12


def test_bondurant_edge_chip_preserves_covered_pixels():
    image = Path(__file__).resolve().parents[3] / "apps/web/public/bondurant-usgs-aerial.webp"
    position = (-93.4208, 41.706)
    _rgb, covered, grid = suggestion.read_demo_chip(image, *position, 350)
    center = suggestion.CHIP_CENTER
    assert covered[center, center]
    assert not covered[:, 0].any()
    x, y = grid @ (center + 0.5, center + 0.5)
    xs, ys = transform("EPSG:3857", "EPSG:4326", [x], [y])
    assert (xs[0], ys[0]) == pytest.approx(position)


def test_cloudless_imagery_is_used_when_recent_sentinel_has_no_contour(monkeypatch):
    position = (1.786, 48.31)
    size = suggestion.CHIP_SIZE
    monkeypatch.setenv("FIELD_SHIFT_BOUNDARY_MODEL_URL", MODEL_URL)
    monkeypatch.setattr(suggestion, "satellite_image_at", lambda *_args: None)
    monkeypatch.setattr(suggestion, "read_wms_chip", lambda *_args: None)
    rgb = np.full((size, size, 3), 75, dtype=np.uint8)
    valid = np.ones((size, size), dtype=bool)
    monkeypatch.setattr(
        suggestion,
        "read_cloudless_chip",
        lambda *_args: (rgb, valid, suggestion.mercator_grid(*position)),
    )
    mask = np.zeros((size, size), dtype=np.uint8)
    mask[100:150, 95:155] = 1
    monkeypatch.setattr(suggestion, "predict_masks", lambda *_args: [mask])

    body = TestClient(create_app()).post(URL, json={"position": position}).json()
    assert body["imagery_date"] is None
    assert body["imagery_source"] == "EOxCloudless 2016"
    assert body["failure_reason"] is None
    assert contains_point(body["geometry"]["coordinates"][0], *position)


def test_french_orthophoto_used_before_satellite_search(monkeypatch):
    position = (1.786, 48.31)
    monkeypatch.setenv("FIELD_SHIFT_BOUNDARY_MODEL_URL", MODEL_URL)
    size = suggestion.CHIP_SIZE
    rgb = np.full((size, size, 3), 75, dtype=np.uint8)
    valid = np.ones((size, size), dtype=bool)

    def orthophoto(endpoint, layer, grid, image_type, version):
        assert endpoint == "https://data.geopf.fr/wms-r"
        assert layer == "ORTHOIMAGERY.ORTHOPHOTOS"
        assert image_type == "image/png"
        assert version == "1.3.0"
        return rgb, valid, grid

    def unexpected_catalog(*_args):
        raise AssertionError("The orthophoto already provides a contour")

    mask = np.zeros((size, size), dtype=np.uint8)
    mask[100:150, 95:155] = 1
    monkeypatch.setattr(suggestion, "read_wms_chip", orthophoto)
    monkeypatch.setattr(suggestion, "satellite_image_at", unexpected_catalog)
    monkeypatch.setattr(suggestion, "predict_masks", lambda *_args: [mask])

    body = TestClient(create_app()).post(URL, json={"position": position}).json()
    assert body["imagery_source"] == "IGN France orthophoto"
    assert contains_point(body["geometry"]["coordinates"][0], *position)


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [("empty", "no_contour"), ("timeout", "model_unavailable"), ("recover", None)],
)
def test_cloudless_wider_window_retries_no_contour_but_not_model_outage(
    monkeypatch, outcome, expected
):
    position = (16.77, 52.19)
    size = suggestion.CHIP_SIZE
    monkeypatch.setenv("FIELD_SHIFT_BOUNDARY_MODEL_URL", MODEL_URL)
    monkeypatch.setattr(suggestion, "satellite_image_at", lambda *_args: None)
    windows = []

    def image(lon, lat, width):
        windows.append(width)
        rgb = np.full((size, size, 3), 75, dtype=np.uint8)
        valid = np.ones((size, size), dtype=bool)
        return rgb, valid, suggestion.mercator_grid(lon, lat, width)

    def predict(*_args):
        if outcome == "timeout":
            raise TimeoutError
        if outcome == "empty" or len(windows) == 1:
            return []
        mask = np.zeros((size, size), dtype=np.uint8)
        mask[100:150, 95:155] = 1
        return [mask]

    monkeypatch.setattr(suggestion, "read_cloudless_chip", image)
    monkeypatch.setattr(suggestion, "predict_masks", predict)
    body = TestClient(create_app()).post(URL, json={"position": position}).json()
    assert windows == (
        [suggestion.EOX_WINDOW_METERS] if outcome == "timeout"
        else [suggestion.EOX_WINDOW_METERS, suggestion.EOX_WIDE_WINDOW_METERS]
    )
    assert body["failure_reason"] == expected
    if outcome == "recover":
        assert contains_point(body["geometry"]["coordinates"][0], *position)
    else:
        assert body["geometry"] is None


@pytest.mark.parametrize(("gap_size", "expected"), [(1, True), (13, False)])
def test_mask_with_internal_gap_obeys_area_budget(gap_size, expected):
    longitude, latitude = LOCATION
    epsg = 32634
    xs, ys = transform("EPSG:4326", f"EPSG:{epsg}", [longitude], [latitude])
    radius = (suggestion.CHIP_CENTER + 0.5) * suggestion.RESOLUTION_M
    grid = suggestion.from_origin(xs[0] - radius, ys[0] + radius, 10, 10)
    mask = np.zeros((256, 256), dtype=np.uint8)
    mask[90:165, 90:165] = 1
    mask[100 : 100 + gap_size, 100 : 100 + gap_size] = 0
    assert (suggestion.contour_at(mask, grid, epsg, longitude, latitude) is not None) is expected
