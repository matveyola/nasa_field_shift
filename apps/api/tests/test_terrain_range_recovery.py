"""Exercise real GDAL range caching against a deterministic local faulting server."""

import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import numpy as np
import pytest
import rasterio
from rasterio.errors import RasterioIOError
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from field_shift.modules.imagery.vegetation_raster import read_window
from field_shift.modules.terrain.dem import TerrainUnavailableError, _read_tile


@pytest.mark.parametrize(
    ("reader", "read_error"), [("terrain", TerrainUnavailableError), ("sentinel", RasterioIOError)]
)
def test_truncated_range_does_not_poison_the_next_attempt_in_the_same_process(reader, read_error):
    elevation = np.random.default_rng(2026).uniform(100, 200, size=(512, 512)).astype("float32")
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff",
            width=512,
            height=512,
            count=1,
            dtype="float32",
            crs="EPSG:4326",
            transform=from_origin(30, 51, 1 / 3600, 1 / 3600),
            tiled=True,
            blockxsize=256,
            blockysize=256,
            compress="deflate",
        ) as dataset:
            dataset.write(elevation, 1)
        body = memory.read()

    class RangeServer(BaseHTTPRequestHandler):
        fail_ranges = True
        requests = 0
        truncated_ranges = 0

        def log_message(self, *_args):
            return

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

        def do_GET(self):
            type(self).requests += 1
            match = re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers.get("Range", ""))
            if match is None:
                self.send_error(416)
                return
            start, end = map(int, match.groups())
            end = min(end, len(body) - 1)
            payload = body[start : end + 1]
            if self.fail_ranges and start > 0:
                # HTTP completes, but the returned tile range is shorter than requested.
                # This reproduces a cached partial TIFF block rather than a connection error.
                payload = payload[: max(1, len(payload) // 2)]
                type(self).truncated_ranges += 1
            self.send_response(206)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), RangeServer)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/dem.tif"

        def read():
            if reader == "terrain":
                return _read_tile(url, (30.09, 50.87, 30.13, 50.91))
            transform = from_origin(30.09, 50.91, 1 / 3600, 1 / 3600)
            return read_window(url, transform, (144, 144), 4326, categorical=False), transform

        outer_cache_setting = "/vsicurl/https://unrelated.example/imagery.tif"
        with rasterio.Env(
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
            GDAL_HTTP_TIMEOUT="3",
            GDAL_HTTP_MAX_RETRY="0",
            CPL_VSIL_CURL_NON_CACHED=outer_cache_setting,
        ):
            with pytest.raises(read_error):
                read()
            assert RangeServer.truncated_ranges > 0
            assert rasterio.env.get_gdal_config("CPL_VSIL_CURL_NON_CACHED") == outer_cache_setting
            failed_requests = RangeServer.requests
            RangeServer.fail_ranges = False

            recovered, affine = read()
            assert RangeServer.requests > failed_requests
            assert rasterio.env.get_gdal_config("CPL_VSIL_CURL_NON_CACHED") == outer_cache_setting
            # Compare the successfully recovered geographic window with the known source,
            # proving the failed attempt did not leave zero-filled or partial elevations.
            col, row = (~from_origin(30, 51, 1 / 3600, 1 / 3600)) * (affine.c, affine.f)
            row, col = round(row), round(col)
            np.testing.assert_array_equal(
                recovered, elevation[row : row + recovered.shape[0], col : col + recovered.shape[1]]
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()
