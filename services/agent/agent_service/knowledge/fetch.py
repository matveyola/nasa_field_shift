"""Fetch allowlisted source pages into a local cache. Only URLs in SOURCES can be fetched.

    uv run python -m agent_service.knowledge fetch
"""

import hashlib
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from .sources import ALLOWED_URLS, SOURCES, SourcePage

log = logging.getLogger("agent_service.knowledge.fetch")

USER_AGENT = "FieldShift-knowledge-ingest/0.3 (non-commercial research prototype; NASA Space Apps 2026)"
MAX_BYTES = 2_000_000
MAX_PDF_BYTES = 8_000_000
TIMEOUT_S = 30
RETRIES = 3
PAUSE_S = 1.0  # between requests: be gentle with fao.org


class FetchError(Exception):
    pass


@dataclass
class FetchRecord:
    id: str
    url: str
    status: str            # fetched | unchanged | failed
    sha256: str | None
    bytes: int
    retrieved_at: str
    http_last_modified: str | None
    error: str | None = None


class _SameHostRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).netloc != urlsplit(req.full_url).netloc or not newurl.startswith("https://"):
            raise FetchError(f"refused redirect off the allowlisted host: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SameHostRedirects())


def fetch_bytes(url: str, fmt: str = "html") -> tuple[bytes, str | None]:
    """GET one allowlisted URL with bounded retries, timeout and size; the content type must match `fmt`."""
    limit = MAX_PDF_BYTES if fmt == "pdf" else MAX_BYTES
    if url not in ALLOWED_URLS:
        raise FetchError(f"not allowlisted: {url}")
    last: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with _OPENER.open(request, timeout=TIMEOUT_S) as response:
                content_type = response.headers.get("Content-Type", "")
                if fmt not in content_type:
                    raise FetchError(f"unexpected content type {content_type!r}")
                body = response.read(limit + 1)
                if len(body) > limit:
                    raise FetchError("response larger than the size limit")
                return body, response.headers.get("Last-Modified")
        except FetchError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            status = getattr(e, "code", None)
            if status is not None and 400 <= status < 500 and status != 429:
                break  # not retryable
            time.sleep(PAUSE_S * 2 ** attempt)
    raise FetchError(f"fetch failed after retries: {type(last).__name__}: {last}")


def fetch_all(raw_dir: Path, sources: tuple[SourcePage, ...] = SOURCES) -> list[FetchRecord]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for i, source in enumerate(sources):
        if i:
            time.sleep(PAUSE_S)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        path = raw_dir / f"{source.id}.{source.fmt}"
        try:
            body, last_modified = fetch_bytes(source.url, source.fmt)
        except FetchError as e:
            log.error("fetch failed id=%s error=%s", source.id, e)
            records.append(FetchRecord(source.id, source.url, "failed", None, 0, now, None, str(e)))
            continue
        digest = hashlib.sha256(body).hexdigest()
        old = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        if old != digest:
            path.write_bytes(body)
        records.append(FetchRecord(source.id, source.url, "unchanged" if old == digest else "fetched", digest,
                                   len(body), now, last_modified))
        log.info("fetched id=%s bytes=%d sha256=%s status=%s", source.id, len(body), digest[:12], records[-1].status)
    (raw_dir / "fetch_manifest.json").write_text(json.dumps([asdict(r) for r in records], indent=2), encoding="utf-8")
    return records
