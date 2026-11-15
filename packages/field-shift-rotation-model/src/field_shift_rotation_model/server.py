"""Private HTTP boundary: one isolated bounded child per accepted operation."""

import json
import multiprocessing
import os
import resource
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore

MAX_BYTES = 24 * 1024 * 1024
TIMEOUT_SECONDS = 50
MEMORY_BYTES = 2 * 1024**3


def child(connection, operation, payload):
    try:
        os.environ["OPENBLAS_NUM_THREADS"] = "1"
        os.environ["OMP_NUM_THREADS"] = "1"
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
        resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT_SECONDS, TIMEOUT_SECONDS + 1))
        from .runtime import dispatch

        result = dispatch(operation, payload)
        raw = json.dumps(result, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) > MAX_BYTES:
            raise ValueError("Runtime output exceeds 24 MiB")
        connection.send_bytes(b"200\n" + raw)
    except ValueError as error:
        connection.send_bytes(b"422\n" + json.dumps({"detail": str(error)[:500]}).encode())
    except Exception:
        connection.send_bytes(
            b"503\n" + b'{"detail":"Required model inputs or provider are unavailable; '
            b'no substitute result"}'
        )
    finally:
        connection.close()


def execute(operation, payload, *, timeout=TIMEOUT_SECONDS):
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=child, args=(writer, operation, payload))
    process.start()
    writer.close()
    try:
        if not reader.poll(timeout):
            return 503, {"detail": "Model execution time bound exceeded"}
        message = reader.recv_bytes(MAX_BYTES + 1024)
        status, data = message.split(b"\n", 1)
        return int(status), json.loads(data)
    except (EOFError, OSError, ValueError):
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
            if not compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                return self.send_json(401, {"detail": "Private service authentication required"})
            if self.path not in {"/catalog", "/acquire", "/evaluate"}:
                return self.send_json(404, {"detail": "Unknown operation"})
            try:
                size = int(self.headers.get("Content-Length", "-1"))
                if not 0 <= size <= MAX_BYTES:
                    raise ValueError
            except ValueError:
                return self.send_json(413, {"detail": "Bounded Content-Length required"})
            request_slot = catalog_slot if self.path == "/catalog" and catalog is not None else slot
            if not request_slot.acquire(blocking=False):
                return self.send_json(503, {"detail": "Model execution slot is busy"})
            try:
                self.connection.settimeout(10)
                raw = self.rfile.read(size)
                if len(raw) != size:
                    return self.send_json(400, {"detail": "Incomplete body"})
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError
                if self.path == "/catalog" and catalog is not None:
                    status, result = 200, catalog
                else:
                    status, result = execute(self.path[1:], payload)
                self.send_json(status, result)
            except (ValueError, TimeoutError):
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
