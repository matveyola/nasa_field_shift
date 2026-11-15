"""Real loopback HTTP checks for the private crop-model client; the card is synthetic."""

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from field_shift_contracts.planning_v1 import ModelCard

from field_shift.modules.planning_runtime import transport
from field_shift.modules.planning_runtime.transport import ModelClient, ModelUnavailable

TOKEN = "t" * 32
BUNDLE = SimpleNamespace(hydraulics={}, footprint=None)


@contextmanager
def crop_service(status, body, delay=0.0):
    delivered = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            time.sleep(delay)
            data = json.dumps(body).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                self.wfile.flush()
                delivered.append(self.path)
            except OSError:
                delivered.append("broken:" + self.path)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", delivered
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def card(example):
    return ModelCard.model_validate_json(json.dumps(example["model_card"]))


def configure(monkeypatch, url):
    monkeypatch.setenv("FIELD_SHIFT_CROP_MODEL_URL", url)
    monkeypatch.setenv("FIELD_SHIFT_CROP_MODEL_TOKEN", TOKEN)


def test_slow_but_healthy_model_card_is_accepted(monkeypatch, card):
    # Reproduces the observed 7.4-7.9 s cold card on a 1-CPU container; the old 5 s bound failed.
    with crop_service(200, card.model_dump(mode="json"), delay=8.0) as (url, delivered):
        configure(monkeypatch, url)
        assert ModelClient().card(BUNDLE) == card
    assert delivered == ["/card"]


def test_unresponsive_model_card_fails_explicitly(monkeypatch, card):
    # The real bound is scaled down so the timeout path runs quickly.
    monkeypatch.setattr(transport, "CARD_TIMEOUT_SECONDS", 0.5)
    with crop_service(200, card.model_dump(mode="json"), delay=2.0) as (url, _delivered):
        configure(monkeypatch, url)
        with pytest.raises(ModelUnavailable, match="no substitute result"):
            ModelClient().card(BUNDLE)


@pytest.mark.parametrize("status", [503, 403])
def test_busy_or_rejecting_model_service_fails_explicitly(monkeypatch, status):
    with crop_service(status, {"status": "busy"}) as (url, delivered):
        configure(monkeypatch, url)
        with pytest.raises(ModelUnavailable, match="no substitute result"):
            ModelClient().card(BUNDLE)
    assert delivered == ["/card"]


def test_unreachable_or_unconfigured_model_fails_explicitly(monkeypatch):
    with crop_service(200, {}) as (url, _delivered):
        pass  # The port is closed once the service has stopped.
    configure(monkeypatch, url)
    with pytest.raises(ModelUnavailable, match="no substitute result"):
        ModelClient().card(BUNDLE)
    monkeypatch.setenv("FIELD_SHIFT_CROP_MODEL_TOKEN", "short")
    with pytest.raises(ModelUnavailable, match="not configured"):
        ModelClient().card(BUNDLE)
