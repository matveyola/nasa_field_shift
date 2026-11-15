"""Private HTTP boundary: one isolated bounded child per accepted operation."""

import json
import logging
import multiprocessing
import os
import resource
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore
from time import monotonic
from urllib.error import HTTPError
from uuid import UUID, uuid4

MAX_BYTES = 24 * 1024 * 1024
TIMEOUT_SECONDS = 50
MEMORY_BYTES = 2 * 1024**3
LOG = logging.getLogger(__name__)
PHASES = frozenset({"catalog", "acquire", "evaluate"})
STAGES = frozenset(
    {"setup", "dispatch", "serialize", "send", "wait", "receive", "admission", "read"}
)
CATEGORIES = frozenset(
    {
        "http_error",
        "timeout",
        "io_error",
        "memory_error",
        "json_decode",
        "invalid_value",
        "unexpected_exception",
        "time_bound",
        "process_channel",
        "content_length",
        "busy",
        "incomplete_body",
    }
)


def valid_request_id(value):
    """An opaque correlation label grants no authority and carries no raw input."""
    if not isinstance(value, str) or len(value) != 36:
        return None
    try:
        return value if str(UUID(value)) == value else None
    except ValueError:
        return None


def failure_event(operation, stage, category, started, *, status=503, request_id=None):
    """Private bounded metadata only: never exception text, payloads or URLs."""
    try:
        LOG.warning(
            json.dumps(
                {
                    "event": "rotation_runtime_failure",
                    "phase": operation
                    if isinstance(operation, str) and operation in PHASES
                    else "unknown",
                    "stage": stage if stage in STAGES else "unknown",
                    "category": category if category in CATEGORIES else "unknown",
                    "status": status if type(status) is int and 100 <= status <= 599 else None,
                    "request_id": valid_request_id(request_id),
                    "elapsed_ms": min(3_600_000, max(0, int((monotonic() - started) * 1000))),
                },
                separators=(",", ":"),
            )
        )
    except Exception:
        pass  # Diagnostics cannot override the original public failure.


def exception_category(error):
    # Closed categories deliberately do not assert the root cause of IO failures.
    if isinstance(error, HTTPError):
        return "http_error"
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, OSError):
        return "io_error"
    if isinstance(error, MemoryError):
        return "memory_error"
    if isinstance(error, json.JSONDecodeError):
        return "json_decode"
    if isinstance(error, ValueError):
        return "invalid_value"
    return "unexpected_exception"


def child(connection, operation, payload, request_id=None):
    started, stage = monotonic(), "setup"
    request_id = valid_request_id(request_id) or str(uuid4())
    try:
        os.environ["OPENBLAS_NUM_THREADS"] = "1"
        os.environ["OMP_NUM_THREADS"] = "1"
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT_SECONDS, TIMEOUT_SECONDS + 1))
        from .runtime import dispatch

        stage = "dispatch"
        result = dispatch(operation, payload)
        stage = "serialize"
        raw = json.dumps(result, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) > MAX_BYTES:
            raise ValueError("Runtime output exceeds 24 MiB")
        stage = "send"
        connection.send_bytes(b"200\n" + raw)
    except ValueError as error:
        failure_event(
            operation, stage, exception_category(error), started, status=422, request_id=request_id
        )
        connection.send_bytes(b"422\n" + json.dumps({"detail": str(error)[:500]}).encode())
    except Exception as error:
        failure_event(operation, stage, exception_category(error), started, request_id=request_id)
        connection.send_bytes(
            b"503\n" + b'{"detail":"Required model inputs or provider are unavailable; '
            b'no substitute result"}'
        )
    finally:
        connection.close()


def execute(operation, payload, *, timeout=TIMEOUT_SECONDS, request_id=None):
    started = monotonic()
    request_id = valid_request_id(request_id) or str(uuid4())
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=child, args=(writer, operation, payload, request_id))
    process.start()
    writer.close()
    try:
        if not reader.poll(timeout):
            failure_event(operation, "wait", "time_bound", started, request_id=request_id)
            return 503, {"detail": "Model execution time bound exceeded"}
        message = reader.recv_bytes(MAX_BYTES + 1024)
        status, data = message.split(b"\n", 1)
        return int(status), json.loads(data)
    except (EOFError, OSError, ValueError):
        failure_event(operation, "receive", "process_channel", started, request_id=request_id)
        return 503, {"detail": "Model process failed within its resource bound"}
    finally:
        reader.close()
        if process.is_alive():
            process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join()


def handler(token, *, catalog=None):
    slot = BoundedSemaphore(1)
    catalog_slot = BoundedSemaphore(1)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, status, body):
            data = json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                return self.send_json(401, {"detail": "Private service authentication required"})
            if self.path != "/health":
                return self.send_json(404, {"detail": "Unknown operation"})
            self.send_json(200, {"status": "ready", "scope": "private_http_boundary"})

        def do_POST(self):
            started = monotonic()
            if not compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                return self.send_json(401, {"detail": "Private service authentication required"})
            if self.path not in {"/catalog", "/acquire", "/evaluate"}:
                return self.send_json(404, {"detail": "Unknown operation"})
            request_id = valid_request_id(self.headers.get("X-Request-ID")) or str(uuid4())
            try:
                size = int(self.headers.get("Content-Length", "-1"))
                if not 0 <= size <= MAX_BYTES:
                    raise ValueError
            except ValueError:
                failure_event(
                    self.path[1:],
                    "admission",
                    "content_length",
                    started,
                    status=413,
                    request_id=request_id,
                )
                return self.send_json(413, {"detail": "Bounded Content-Length required"})
            request_slot = catalog_slot if self.path == "/catalog" and catalog is not None else slot
            if not request_slot.acquire(blocking=False):
                failure_event(self.path[1:], "admission", "busy", started, request_id=request_id)
                return self.send_json(503, {"detail": "Model execution slot is busy"})
            try:
                self.connection.settimeout(10)
                raw = self.rfile.read(size)
                if len(raw) != size:
                    failure_event(
                        self.path[1:],
                        "read",
                        "incomplete_body",
                        started,
                        status=400,
                        request_id=request_id,
                    )
                    return self.send_json(400, {"detail": "Incomplete body"})
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError
                if self.path == "/catalog" and catalog is not None:
                    status, result = 200, catalog
                else:
                    status, result = execute(self.path[1:], payload, request_id=request_id)
                self.send_json(status, result)
            except (ValueError, TimeoutError) as error:
                failure_event(
                    self.path[1:],
                    "read",
                    exception_category(error),
                    started,
                    status=400,
                    request_id=request_id,
                )
                self.send_json(400, {"detail": "Invalid JSON request"})
            finally:
                request_slot.release()

    return Handler


def main():
    token = os.getenv("FIELD_SHIFT_ROTATION_MODEL_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("A private rotation model token of at least 32 characters is required")
    # Catalog metadata are immutable for this installed engine. Acquire them once
    # within the normal process bound before readiness, without public data access.
    # Reading capabilities must not contend with a long-running field calculation.
    status, catalog = execute("catalog", {})
    if status != 200:
        raise SystemExit("Installed rotation catalog could not initialize")
    server = ThreadingHTTPServer(
        ("0.0.0.0", int(os.getenv("PORT", "8080"))), handler(token, catalog=catalog)
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
