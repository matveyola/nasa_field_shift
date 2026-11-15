"""No model dependencies in the API: bounded authenticated private HTTP only."""

import json
import os
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_BYTES = 24 * 1024 * 1024


class HandoffUnavailable(RuntimeError):
    pass


class HandoffInvalid(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HandoffClient:
    def call(self, phase, payload):
        url = os.getenv("FIELD_SHIFT_ROTATION_MODEL_URL", "").rstrip("/")
        token = os.getenv("FIELD_SHIFT_ROTATION_MODEL_TOKEN", "")
        if not url or len(token) < 32:
            raise HandoffUnavailable("Private rotation model is not configured")
        raw = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) > MAX_BYTES:
            raise HandoffInvalid("Rotation input exceeds 24 MiB")
        request = Request(
            url + "/" + phase,
            data=raw,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        try:
            with build_opener(NoRedirect()).open(
                request, timeout=5 if phase == "catalog" else 55
            ) as response:
                encoded = response.read(MAX_BYTES + 1)
            if len(encoded) > MAX_BYTES:
                raise HandoffUnavailable("Rotation output exceeds byte bound")
            result = json.loads(encoded)
            if not isinstance(result, dict):
                raise HandoffUnavailable("Rotation response is not an object")
            return result
        except HTTPError as error:
            if error.code == 422:
                raise HandoffInvalid(
                    "The rotation model rejected unsupported or incomplete inputs"
                ) from None
            raise HandoffUnavailable(
                "Private rotation model or required public data unavailable"
            ) from None
        except (OSError, ValueError) as error:
            raise HandoffUnavailable(
                "Private rotation model unavailable; no substitute result"
            ) from error
