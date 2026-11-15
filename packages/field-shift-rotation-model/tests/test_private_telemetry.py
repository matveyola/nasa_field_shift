"""Synthetic boundary failures prove private telemetry without provider calls."""

import json
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread
from time import monotonic
from urllib.error import HTTPError
from uuid import UUID

import pytest

from field_shift_rotation_model import runtime, server

REQUEST_ID = "b82a1b50-7897-4ad0-9979-ce5ec9cf363f"
CANARY = "PRIVATE_PAYLOAD_TOKEN_URL_CANARY"


class Connection:
    def __init__(self):
        self.sent = []
        self.closed = False

    def send_bytes(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


@pytest.fixture
def direct_child(monkeypatch):
    limits = []
    monkeypatch.setattr(server.resource, "setrlimit", lambda *args: limits.append(args))
    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"):
        monkeypatch.setenv(key, "1")
    return limits


def event(caplog):
    records = [r for r in caplog.records if r.name == server.__name__]
    assert len(records) == 1
    assert CANARY not in caplog.text
    result = json.loads(records[0].getMessage())
    assert set(result) == {
        "event",
        "phase",
        "stage",
        "category",
        "status",
        "request_id",
        "elapsed_ms",
    }
    assert 0 <= result["elapsed_ms"] <= 3_600_000
    return result


@pytest.mark.parametrize(
    "error,category,status",
    [
        (HTTPError("https://private.invalid/" + CANARY, 503, CANARY, {}, None), "http_error", 503),
        (TimeoutError(CANARY), "timeout", 503),
        (OSError(CANARY), "io_error", 503),
        (MemoryError(CANARY), "memory_error", 503),
        (json.JSONDecodeError(CANARY, CANARY, 0), "json_decode", 422),
        (ValueError(CANARY), "invalid_value", 422),
        (RuntimeError(CANARY), "unexpected_exception", 503),
    ],
)
def test_dispatch_exception_categories_are_closed_and_redacted(
    direct_child, monkeypatch, caplog, error, category, status
):
    def fail(*args):
        raise error

    monkeypatch.setattr(runtime, "dispatch", fail)
    connection = Connection()
    server.child(connection, "acquire", {"secret": CANARY}, REQUEST_ID)
    assert connection.closed and len(connection.sent) == 1
    assert connection.sent[0].startswith(f"{status}\n".encode())
    detail = json.loads(connection.sent[0].split(b"\n", 1)[1])["detail"]
    # Keep the existing public422 description contract; new private logs never copy it.
    assert (
        detail == str(error)[:500]
        if status == 422
        else detail == ("Required model inputs or provider are unavailable; no substitute result")
    )
    row = event(caplog)
    assert (row["phase"], row["stage"], row["category"], row["status"], row["request_id"]) == (
        "acquire",
        "dispatch",
        category,
        status,
        REQUEST_ID,
    )
    assert direct_child == [
        (server.resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3)),
        (server.resource.RLIMIT_CPU, (50, 51)),
    ]


def test_success_never_logs_the_payload_or_result(direct_child, monkeypatch, caplog):
    monkeypatch.setattr(runtime, "dispatch", lambda phase, payload: {"result": CANARY})
    connection = Connection()
    server.child(connection, "evaluate", {"input": CANARY}, REQUEST_ID)
    assert connection.sent == [
        b"200\n" + json.dumps({"result": CANARY}, separators=(",", ":")).encode()
    ]
    assert not [r for r in caplog.records if r.name == server.__name__]


def test_serialization_failure_retains_phase_without_raw_object(direct_child, monkeypatch, caplog):
    monkeypatch.setattr(runtime, "dispatch", lambda *args: {"secret": {CANARY}})
    connection = Connection()
    server.child(connection, "evaluate", {}, REQUEST_ID)
    assert connection.sent[0].startswith(b"503\n")
    row = event(caplog)
    assert (row["stage"], row["category"]) == ("serialize", "unexpected_exception")


@pytest.mark.parametrize(
    "value", ["", CANARY, "\r\n" + CANARY, "a" * 1000, {}, None, REQUEST_ID.upper()]
)
def test_invalid_correlation_is_never_an_accepted_label(value):
    assert server.valid_request_id(value) is None


def test_actual_spawn_propagates_id_to_child_failure_logs(capfd):
    status, result = server.execute("unsupported", {"secret": CANARY}, request_id=REQUEST_ID)
    assert status == 422 and result == {"detail": "Unsupported runtime phase"}
    captured = capfd.readouterr()
    assert REQUEST_ID in captured.err and CANARY not in captured.err
    assert '"category":"invalid_value"' in captured.err and '"phase":"unknown"' in captured.err


def test_actual_timeout_retains_id_and_existing_process_bound(caplog):
    import multiprocessing

    previous = {p.pid for p in multiprocessing.active_children()}
    status, result = server.execute("catalog", {}, timeout=0, request_id=REQUEST_ID)
    assert status == 503 and result == {"detail": "Model execution time bound exceeded"}
    assert {p.pid for p in multiprocessing.active_children()} <= previous
    row = event(caplog)
    assert (row["request_id"], row["stage"], row["category"]) == (REQUEST_ID, "wait", "time_bound")


def test_unknown_labels_and_logging_sink_failures_cannot_escape(caplog, monkeypatch):
    server.failure_event(CANARY, CANARY, CANARY, monotonic(), status=999, request_id=CANARY)
    row = event(caplog)
    assert (row["phase"], row["stage"], row["category"], row["status"], row["request_id"]) == (
        "unknown",
        "unknown",
        "unknown",
        None,
        None,
    )
    monkeypatch.setattr(
        server.LOG, "warning", lambda *a, **kw: (_ for _ in ()).throw(OSError(CANARY))
    )
    server.failure_event("acquire", "read", "timeout", monotonic(), request_id=REQUEST_ID)


def test_private_header_is_bounded_not_auth_authority(monkeypatch):
    calls = []

    def execute(phase, payload, *, request_id):
        calls.append((phase, payload, request_id))
        return 503, {"detail": "Explicit synthetic model failure"}

    monkeypatch.setattr(server, "execute", execute)
    token = CANARY * 2
    http = ThreadingHTTPServer(("127.0.0.1", 0), server.handler(token))
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()

    def request(correlation, authorized=True):
        connection = HTTPConnection(*http.server_address)
        headers = {"X-Request-ID": correlation}
        if authorized:
            headers["Authorization"] = "Bearer " + token
        try:
            connection.request("POST", "/evaluate", body="{}", headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        assert request(REQUEST_ID, False)[0] == 401
        assert calls == []
        assert request(REQUEST_ID) == (503, {"detail": "Explicit synthetic model failure"})
        assert calls[-1] == ("evaluate", {}, REQUEST_ID)
        request(CANARY * 40)
        assert str(UUID(calls[-1][2])) == calls[-1][2] and calls[-1][2] != REQUEST_ID
    finally:
        http.shutdown()
        http.server_close()
        thread.join(5)
