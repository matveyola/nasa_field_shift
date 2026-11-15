"""No model dependencies in the API: bounded authenticated private HTTP only."""

import json
import logging
import os
from time import monotonic
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID, uuid4

MAX_BYTES = 24 * 1024 * 1024
LOG = logging.getLogger(__name__)
PHASES = frozenset({"catalog", "acquire", "evaluate"})
CATEGORIES = frozenset(
    {
        "not_configured",
        "input_serialization",
        "input_size",
        "output_size",
        "output_shape",
        "http_error",
        "timeout",
        "io_error",
        "json_decode",
        "invalid_value",
    }
)


def valid_request_id(value):
    if not isinstance(value, str) or len(value) != 36:
        return None
    try:
        return value if str(UUID(value)) == value else None
    except ValueError:
        return None


def failure_event(phase, category, started, *, status=None, request_id=None):
    """Do not expose URLs, headers, input fields or exception messages in logs."""
    try:
        LOG.warning(
            json.dumps(
                {
                    "event": "rotation_transport_failure",
                    "phase": phase if isinstance(phase, str) and phase in PHASES else "unknown",
                    "category": category if category in CATEGORIES else "unknown",
                    "request_id": valid_request_id(request_id),
                    "status": status if type(status) is int and 100 <= status <= 599 else None,
                    "elapsed_ms": min(3_600_000, max(0, int((monotonic() - started) * 1000))),
                },
                separators=(",", ":"),
            )
        )
    except Exception:
        pass  # Diagnostics cannot override the original public failure.


class HandoffUnavailable(RuntimeError):
    pass


class HandoffInvalid(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HandoffClient:
    def __init__(self, *, correlation_id=None):
        self.correlation_id = valid_request_id(correlation_id)

    def call(self, phase, payload):
        started = monotonic()
        request_id = self.correlation_id or str(uuid4())
        url = os.getenv("FIELD_SHIFT_ROTATION_MODEL_URL", "").rstrip("/")
        token = os.getenv("FIELD_SHIFT_ROTATION_MODEL_TOKEN", "")
        if not url or len(token) < 32:
            failure_event(phase, "not_configured", started, request_id=request_id)
            raise HandoffUnavailable("Private rotation model is not configured")
        try:
            raw = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        except (TypeError, ValueError):
            failure_event(phase, "input_serialization", started, request_id=request_id)
            raise
        if len(raw) > MAX_BYTES:
            failure_event(phase, "input_size", started, request_id=request_id)
            raise HandoffInvalid("Rotation input exceeds 24 MiB")
        request = Request(
            url + "/" + phase,
            data=raw,
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
                "X-Request-ID": request_id,
            },
        )
        try:
            with build_opener(NoRedirect()).open(
                request, timeout=5 if phase == "catalog" else 55
            ) as response:
                encoded = response.read(MAX_BYTES + 1)
            if len(encoded) > MAX_BYTES:
                failure_event(phase, "output_size", started, request_id=request_id)
                raise HandoffUnavailable("Rotation output exceeds byte bound")
            result = json.loads(encoded)
            if not isinstance(result, dict):
                failure_event(phase, "output_shape", started, request_id=request_id)
                raise HandoffUnavailable("Rotation response is not an object")
            return result
        except HTTPError as error:
            failure_event(phase, "http_error", started, status=error.code, request_id=request_id)
            if error.code == 422:
                raise HandoffInvalid(
                    "The rotation model rejected unsupported or incomplete inputs"
                ) from None
            raise HandoffUnavailable(
                "Private rotation model or required public data unavailable"
            ) from None
        except (OSError, ValueError) as error:
            category = (
                "timeout"
                if isinstance(error, TimeoutError)
                else "io_error"
                if isinstance(error, OSError)
                else "json_decode"
                if isinstance(error, json.JSONDecodeError)
                else "invalid_value"
            )
            failure_event(phase, category, started, request_id=request_id)
            raise HandoffUnavailable(
                "Private rotation model unavailable; no substitute result"
            ) from error
