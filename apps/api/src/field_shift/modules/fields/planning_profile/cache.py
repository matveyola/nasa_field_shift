"""Bounded content-addressed raster asset cache; only operator-configured HTTPS URLs.

Supports pinned WebDAV GeoTIFF tiles or WCS GeoTIFF subset URLs. An outage never
creates substitute soil. Cache age/provenance checks remain the caller's duty.
"""

import hashlib
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class RasterCache:
    def __init__(self, directory, *, max_bytes=64 * 1024 * 1024, timeout_seconds=20):
        self.directory = Path(directory)
        self.max_bytes, self.timeout = max_bytes, timeout_seconds
        if not 0 < max_bytes <= 256 * 1024 * 1024 or not 0 < timeout_seconds <= 30:
            raise ValueError("cache resource bound outside permitted range")

    def acquire(self, url, checksum, *, allowed_hosts):
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in allowed_hosts
            or parsed.username
            or parsed.password
        ):
            raise ValueError("raster URL must be configured credential-free HTTPS")
        if len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
            raise ValueError("expected SHA256 required")
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / f"{checksum}.tif"
        if destination.exists():
            if destination.stat().st_size > self.max_bytes or digest_file(destination) != checksum:
                raise ValueError("corrupt or oversized cached raster")
            return destination
        descriptor, temporary = tempfile.mkstemp(dir=self.directory, suffix=".partial")
        try:
            with (
                os.fdopen(descriptor, "wb") as output,
                urlopen(url, timeout=self.timeout) as response,
            ):
                final = urlparse(response.geturl())
                if final.scheme != "https" or final.hostname not in allowed_hosts:
                    raise ValueError("unconfigured raster redirect")
                size = 0
                deadline = time.monotonic() + 30
                while chunk := response.read(1024 * 1024):
                    if time.monotonic() > deadline:
                        raise TimeoutError("raster download exceeded wall-time bound")
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ValueError("raster download exceeds execution bound")
                    output.write(chunk)
            if digest_file(temporary) != checksum:
                raise ValueError("downloaded raster checksum mismatch")
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return destination
