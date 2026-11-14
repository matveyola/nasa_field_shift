"""Per-request context attestation: the host backend's signed statement about one request (M6B).

The host's backend-for-frontend (BFF) authenticates the farmer, checks that they may access the field,
builds the request's FieldContext from its own store and the geo service, and then signs:

    principal                opaque id of the authenticated farmer (never shown, used for isolation)
    field_id, field_context_revision   the field and context version it authorized
    body_sha256              SHA-256 of the exact request body bytes it sends (binds every value)
    iat, exp                 issue and expiry time, unix seconds; at most MAX_LIFETIME_S apart
    jti                      optional random id, so two signatures over identical bytes still differ
                             (the agent accepts each token once: see api._first_use)

Header:  X-FieldShift-Context-Attestation: v1.<base64url(payload JSON)>.<base64url(HMAC-SHA256)>
         HMAC key = AGENT_CONTEXT_SIGNING_KEY, shared only by the BFF and this service (never the browser).

What a valid attestation means here: field facts in this body are host-attested (not "unverified"), and
`principal` is a verified caller id. What it does NOT mean: that a value is scientifically authentic.
Provenance still says where a value came from; the host is accountable for having copied it faithfully.
Nothing else in a request (session_id, any header, any body field) is ever treated as identity.
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass

HEADER = "x-fieldshift-context-attestation"
VERSION = "v1"
MAX_LIFETIME_S = 300
CLOCK_SKEW_S = 30
_PRINCIPAL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")


@dataclass(frozen=True)
class RequestTrust:
    """What this service may believe about one request. Built only from a verified attestation."""

    principal: str | None = None   # verified caller id, or None
    attested: bool = False          # the host signed exactly this body, field and revision

    @property
    def isolation_scope(self) -> str | None:
        return f"principal:{self.principal}" if self.principal else None


UNTRUSTED = RequestTrust()


class AttestationError(Exception):
    """Why an attestation was refused. The category is safe to return; the token never is."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(key: bytes, *, principal: str, field_id: str, revision: int, body: bytes,
         now: float | None = None, lifetime_s: int = 120) -> str:
    """Reference implementation of what the host backend does (also used by tests)."""
    iat = int(now if now is not None else time.time())
    payload = {"v": 1, "principal": principal, "field_id": field_id, "field_context_revision": revision,
               "body_sha256": hashlib.sha256(body).hexdigest(), "iat": iat, "exp": iat + lifetime_s,
               "jti": secrets.token_urlsafe(12)}
    encoded = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    mac = hmac.new(key, f"{VERSION}.{encoded}".encode(), hashlib.sha256).digest()
    return f"{VERSION}.{encoded}.{_b64(mac)}"


def verify(token: str, key: bytes, *, body: bytes, field_id: str, revision: int, now: float | None = None) -> RequestTrust:
    """Check signature, binding and lifetime. Raises AttestationError with a safe category."""
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != VERSION or len(token) > 4096:
        raise AttestationError("malformed")
    expected = hmac.new(key, f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256).digest()
    try:
        given, payload = _unb64(parts[2]), json.loads(_unb64(parts[1]))
    except (ValueError, json.JSONDecodeError):
        raise AttestationError("malformed") from None
    if not hmac.compare_digest(expected, given):
        raise AttestationError("bad_signature")
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise AttestationError("malformed")
    now = now if now is not None else time.time()
    iat, exp = payload.get("iat"), payload.get("exp")
    if not (isinstance(iat, int) and isinstance(exp, int)) or exp - iat > MAX_LIFETIME_S or iat > now + CLOCK_SKEW_S:
        raise AttestationError("bad_lifetime")
    if now > exp + CLOCK_SKEW_S:
        raise AttestationError("expired")
    if not hmac.compare_digest(str(payload.get("body_sha256", "")), hashlib.sha256(body).hexdigest()):
        raise AttestationError("body_mismatch")      # any value changed after the host signed it
    if payload.get("field_id") != field_id:
        raise AttestationError("field_mismatch")
    if payload.get("field_context_revision") != revision:
        raise AttestationError("revision_mismatch")
    principal = payload.get("principal")
    if not isinstance(principal, str) or not _PRINCIPAL.fullmatch(principal):
        raise AttestationError("bad_principal")
    return RequestTrust(principal=principal, attested=True)
