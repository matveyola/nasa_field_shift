"""Internal token-authenticated inference, one bounded child and no external calls."""

import hmac
import json
import os
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore

MAX_BYTES = 16 * 1024**2
slot = BoundedSemaphore(1)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, status, body):
        data = json.dumps(body, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path != "/health":
            return self.reply(404, {"status": "not_found"})
        configured = len(os.getenv("FIELD_SHIFT_CROP_MODEL_TOKEN", "")) >= 32
        return self.reply(
            200 if configured else 503,
            {"status": "ready" if configured else "unconfigured"},
        )

    def do_POST(self):
        self.connection.settimeout(10)
        token = os.getenv("FIELD_SHIFT_CROP_MODEL_TOKEN", "")
        if len(token) < 32 or not hmac.compare_digest(
            self.headers.get("Authorization", ""), "Bearer " + token
        ):
            return self.reply(403, {"status": "forbidden"})
        if self.path not in {"/card", "/evaluate"}:
            return self.reply(404, {"status": "not_found"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        if not 0 < size <= MAX_BYTES or self.headers.get("Transfer-Encoding"):
            return self.reply(413, {"status": "invalid_size"})
        if not slot.acquire(blocking=False):
            return self.reply(503, {"status": "busy"})
        try:
            data = self.rfile.read(size)
            if len(data) != size:
                return self.reply(400, {"status": "incomplete"})
            with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
                subprocess.run(
                    [sys.executable, "-m", "crop_runtime.worker", self.path[1:]],
                    input=data,
                    stdout=output,
                    stderr=errors,
                    timeout=60,
                    check=True,
                    env=dict(
                        os.environ,
                        OPENBLAS_NUM_THREADS="1",
                        OMP_NUM_THREADS="1",
                        MKL_NUM_THREADS="1",
                    ),
                )
                output.seek(0)
                raw = output.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError("Output too large")
                return self.reply(200, json.loads(raw))
        except (ValueError, OSError, subprocess.SubprocessError):
            return self.reply(503, {"status": "model_unavailable"})
        finally:
            slot.release()


if __name__ == "__main__":
    ThreadingHTTPServer(
        (
            os.getenv("CROP_MODEL_HOST", "0.0.0.0"),
            int(os.getenv("CROP_MODEL_PORT", "8001")),
        ),
        Handler,
    ).serve_forever()
