"""Explicit acquisition or immutable replay; network is denied without a session.

A runtime child executes one request. Its provider thread pool shares this session;
no mutable process state is shared between requests or installed package resources.
"""

import base64
import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from io import BytesIO
from threading import RLock
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request
from urllib.request import build_opener as real_build_opener

MAX_RESPONSE = 4 * 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024
MAX_REQUESTS = 128
HOSTS = frozenset(
    {
        "api.open-meteo.com",
        "archive-api.open-meteo.com",
        "power.larc.nasa.gov",
        "maps.isric.org",
        "ds.nccs.nasa.gov",
        "sdmdataaccess.sc.egov.usda.gov",
    }
)
_ACTIVE = None


class AcquisitionUnavailable(OSError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def request_identity(request):
    request = Request(request) if isinstance(request, str) else request
    url = urlsplit(request.full_url)
    if (
        url.scheme != "https"
        or url.hostname not in HOSTS
        or url.username
        or url.password
        or url.port not in (None, 443)
    ):
        raise AcquisitionUnavailable("Provider URL is outside the fixed public-source allowlist")
    if any(key.lower() in {"authorization", "cookie"} for key, _ in request.header_items()):
        raise AcquisitionUnavailable("Only anonymous public provider requests are supported")
    digest = hashlib.sha256(
        canonical(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "body": base64.b64encode(request.data or b"").decode(),
            }
        )
    ).hexdigest()
    return request, digest


class EvidenceSession:
    def __init__(self, *, acquire=False, records=None):
        self.acquire = acquire
        self.records = dict(records or {})
        self.lock = RLock()
        self.total = 0
        if len(self.records) > MAX_REQUESTS:
            raise ValueError("Too many captured provider responses")
        for row in self.records.values():
            raw = base64.b64decode(row["body_base64"], validate=True)
            if len(raw) > MAX_RESPONSE or hashlib.sha256(raw).hexdigest() != row["sha256"]:
                raise ValueError("Captured response checksum or bound mismatch")
            self.total += len(raw)
        if self.total > MAX_TOTAL:
            raise ValueError("Captured response aggregate bound exceeded")

    def open(self, request, *, timeout=20, context=None, handlers=()):
        request, key = request_identity(request)
        with self.lock:
            if key in self.records:
                return BytesIO(base64.b64decode(self.records[key]["body_base64"]))
            if not self.acquire:
                raise AcquisitionUnavailable(
                    "Required public input is not captured; enable explicit acquisition"
                )
            if len(self.records) >= MAX_REQUESTS:
                raise AcquisitionUnavailable("Public acquisition request bound exceeded")
            parts = [*handlers, NoRedirect()]
            if context is not None:
                parts.append(HTTPSHandler(context=context))
            with real_build_opener(*parts).open(request, timeout=min(timeout, 20)) as response:
                raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE or self.total + len(raw) > MAX_TOTAL:
                raise AcquisitionUnavailable("Public response byte bound exceeded")
            self.total += len(raw)
            self.records[key] = {
                "source_url": request.full_url,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
                "available_at": datetime.now(UTC).isoformat(),
                "kind": "acquired_public_input",
                "body_base64": base64.b64encode(raw).decode(),
            }
            return BytesIO(raw)


@contextmanager
def evidence_session(*, acquire=False, records=None):
    global _ACTIVE
    if _ACTIVE is not None:
        raise RuntimeError("Nested model evidence sessions are not supported")
    current = EvidenceSession(acquire=acquire, records=records)
    _ACTIVE = current
    try:
        yield current
    finally:
        _ACTIVE = None


def urlopen(request, *, timeout=20, context=None):
    if _ACTIVE is None:
        raise AcquisitionUnavailable(
            "Provider access requires an explicit acquisition/replay phase"
        )
    return _ACTIVE.open(request, timeout=timeout, context=context)


def build_opener(*handlers):
    class Opener:
        def open(self, request, timeout=20):
            if _ACTIVE is None:
                raise AcquisitionUnavailable("Provider access requires an explicit evidence phase")
            return _ACTIVE.open(request, timeout=timeout, handlers=handlers)

    return Opener()


def acquisition_time():
    """Preserve actual captured availability when offline provider decoding is repeated."""
    if _ACTIVE is not None and _ACTIVE.records:
        return max(row["available_at"] for row in _ACTIVE.records.values())
    return datetime.now(UTC).isoformat(timespec="seconds")
