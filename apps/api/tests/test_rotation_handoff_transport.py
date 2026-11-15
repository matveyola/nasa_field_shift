"""Synthetic private HTTP failures test telemetry, never provider/model outcomes."""

import io
import json
from time import monotonic
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from uuid import UUID

import pytest

from field_shift.modules.rotations.handoff import transport

REQUEST_ID = "b82a1b50-7897-4ad0-9979-ce5ec9cf363f"
CANARY = "PRIVATE_PAYLOAD_TOKEN_URL_CANARY"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("FIELD_SHIFT_ROTATION_MODEL_URL", "http://private.invalid/" + CANARY)
    monkeypatch.setenv("FIELD_SHIFT_ROTATION_MODEL_TOKEN", CANARY * 2)
    calls = []

    def install(result=b'{"ok":true}', error=None):
        def open_request(request, timeout):
            calls.append((request, timeout))
            if error:
                raise error
            return io.BytesIO(result)

        def opener(*handlers):
            assert len(handlers) == 1 and isinstance(handlers[0], transport.NoRedirect)
            return SimpleNamespace(open=open_request)

        monkeypatch.setattr(transport, "build_opener", opener)

    install()
    return calls, install


def events(caplog):
    rows = [json.loads(r.getMessage()) for r in caplog.records if r.name == transport.__name__]
    assert CANARY not in caplog.text
    for row in rows:
        assert set(row) == {"event", "phase", "category", "request_id", "status", "elapsed_ms"}
        assert 0 <= row["elapsed_ms"] <= 3_600_000
        assert row["request_id"] is None or str(UUID(row["request_id"])) == row["request_id"]
    return rows


def test_success_keeps_body_limits_auth_and_durable_correlation(configured, caplog):
    calls, _ = configured
    client = transport.HandoffClient(correlation_id=REQUEST_ID)
    payload = {"synthetic_fixture": CANARY}
    assert client.call("acquire", payload) == {"ok": True}
    assert client.call("evaluate", payload) == {"ok": True}
    assert events(caplog) == []
    assert [timeout for _, timeout in calls] == [55, 55]
    for request, _ in calls:
        assert request.get_header("X-request-id") == REQUEST_ID
        assert request.get_header("Authorization") == "Bearer " + CANARY * 2
        assert json.loads(request.data) == payload


@pytest.mark.parametrize("invalid", [None, "", CANARY, "\r\n" + CANARY, "x" * 1000, 42, {}])
def test_invalid_or_absent_id_is_replaced_without_logging_it(configured, caplog, invalid):
    calls, _ = configured
    client = transport.HandoffClient(correlation_id=invalid)
    client.call("catalog", {})
    client.call("catalog", {})
    identifiers = [r.get_header("X-request-id") for r, _ in calls]
    assert len(set(identifiers)) == 2
    assert all(str(UUID(value)) == value for value in identifiers)
    assert [timeout for _, timeout in calls] == [5, 5]
    assert events(caplog) == []


@pytest.mark.parametrize(
    "code,kind",
    [
        (422, transport.HandoffInvalid),
        (302, transport.HandoffUnavailable),
        (503, transport.HandoffUnavailable),
    ],
)
def test_http_error_logs_only_status_and_id_with_unchanged_public_error(
    configured, caplog, code, kind
):
    _, install = configured
    install(error=HTTPError("https://secret.invalid/" + CANARY, code, CANARY, {}, None))
    with pytest.raises(kind) as caught:
        transport.HandoffClient(correlation_id=REQUEST_ID).call("acquire", {"secret": CANARY})
    expected = (
        "The rotation model rejected unsupported or incomplete inputs"
        if code == 422
        else "Private rotation model or required public data unavailable"
    )
    assert str(caught.value) == expected
    (row,) = events(caplog)
    assert (row["category"], row["status"], row["request_id"], row["phase"]) == (
        "http_error",
        code,
        REQUEST_ID,
        "acquire",
    )


@pytest.mark.parametrize(
    "error,category",
    [
        (TimeoutError(CANARY), "timeout"),
        (URLError(CANARY), "io_error"),
        (ValueError(CANARY), "invalid_value"),
    ],
)
def test_io_failure_categories_do_not_claim_provider_causes(configured, caplog, error, category):
    _, install = configured
    install(error=error)
    with pytest.raises(transport.HandoffUnavailable, match="no substitute result"):
        transport.HandoffClient(correlation_id=REQUEST_ID).call("evaluate", {})
    (row,) = events(caplog)
    assert row["category"] == category and row["status"] is None


@pytest.mark.parametrize(
    "encoded,category", [(b"not-json", "json_decode"), (b"[]", "output_shape")]
)
def test_invalid_response_is_private_and_non_success(configured, caplog, encoded, category):
    _, install = configured
    install(result=encoded)
    with pytest.raises(transport.HandoffUnavailable):
        transport.HandoffClient(correlation_id=REQUEST_ID).call("evaluate", {})
    assert events(caplog)[0]["category"] == category


def test_byte_bounds_and_missing_configuration_remain_fail_closed(configured, monkeypatch, caplog):
    calls, install = configured
    monkeypatch.setattr(transport, "MAX_BYTES", 8)
    with pytest.raises(transport.HandoffInvalid, match="input exceeds"):
        transport.HandoffClient().call("acquire", {"secret": CANARY})
    assert calls == []
    install(result=b"0123456789")
    with pytest.raises(transport.HandoffUnavailable, match="output exceeds"):
        transport.HandoffClient().call("evaluate", {})
    monkeypatch.delenv("FIELD_SHIFT_ROTATION_MODEL_TOKEN")
    with pytest.raises(transport.HandoffUnavailable, match="not configured"):
        transport.HandoffClient().call("catalog", {})
    assert [r["category"] for r in events(caplog)] == [
        "input_size",
        "output_size",
        "not_configured",
    ]


def test_untrusted_labels_and_broken_log_sink_cannot_change_failure(
    configured, monkeypatch, caplog
):
    transport.failure_event(CANARY, CANARY, monotonic(), status=999, request_id=CANARY)
    (row,) = events(caplog)
    assert (row["phase"], row["category"], row["status"], row["request_id"]) == (
        "unknown",
        "unknown",
        None,
        None,
    )
    _, install = configured
    install(error=TimeoutError(CANARY))
    monkeypatch.setattr(
        transport.LOG, "warning", lambda *args, **kw: (_ for _ in ()).throw(OSError(CANARY))
    )
    with pytest.raises(transport.HandoffUnavailable, match="no substitute result"):
        transport.HandoffClient().call("acquire", {})
