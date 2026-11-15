"""The host side of the agent trust boundary: what the API vouches for when it signs a turn.

Standard library only, so the agent's tests can load this exact file and check it against the
agent's verifier (services/agent/tests/test_host_signing.py).

The signature (HMAC over the exact body) says: this body came from the Field Shift API, for this
field and boundary revision, on behalf of this caller. It does not make browser-supplied parts true:
- `field_context` is always built by the API from its own records (never the browser's);
- `history` is forwarded as the browser sent it; the agent uses it as context, never as evidence;
- `client.capabilities` are cut down to what this app executes (HOST_ACTIONS, HOST_SCREENS), so a
  browser can never advertise more than the host does.
"""

import base64
import hashlib
import hmac
import json
import secrets
from time import time

# The widget AgentRequest keys the API forwards.
ASSISTANT_KEYS = ("request_id", "session_id", "locale", "message", "history", "client")
# What the app's assistant host executes: keep in step with
# apps/web/src/features/assistant/Assistant.tsx (its capabilities and onAction).
HOST_ACTIONS = ("map.set_layers", "map.focus_field", "chart.focus", "app.open_screen")
HOST_SCREENS = ("fields", "water", "rotations", "farm")
# The shared workspace of local (single-farm, no sign-in) mode: every request is the same caller.
LOCAL_PRINCIPAL = "field-shift-local-farm"
ATTESTATION_TTL_S = 120


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def agent_principal(user_id: int | None, farm_id: int) -> str:
    """The attested caller: the signed-in member (team mode), else the shared local workspace."""
    return LOCAL_PRINCIPAL if user_id is None else f"farm-{int(farm_id)}:user-{int(user_id)}"


def attestation(key: bytes, *, principal: str, field_id: str, revision: int, body: bytes) -> str:
    issued = int(time())
    payload = {
        "v": 1,
        "principal": principal,
        "field_id": field_id,
        "field_context_revision": revision,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "iat": issued,
        "exp": issued + ATTESTATION_TTL_S,
        "jti": secrets.token_urlsafe(12),
    }
    encoded = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = hmac.digest(key, f"v1.{encoded}".encode(), "sha256")
    return f"v1.{encoded}.{_b64(signature)}"


def host_client(client: object) -> object:
    """The browser's `client` with capabilities limited to what this host executes. Anything
    malformed is passed on unchanged, for the agent's contract validation to refuse."""
    if not isinstance(client, dict) or not isinstance(client.get("capabilities"), dict):
        return client
    caps = client["capabilities"]
    actions, screens = caps.get("actions", []), caps.get("screens", [])
    if not isinstance(actions, list) or not isinstance(screens, list):
        return client
    offered = dict.fromkeys(a for a in actions if isinstance(a, str))
    return {
        **client,
        "capabilities": {
            **caps,
            "actions": [a for a in offered if a in HOST_ACTIONS],
            "screens": [
                s for s in screens if isinstance(s, dict) and s.get("screen_id") in HOST_SCREENS
            ],
        },
    }


def assistant_body(payload: dict, field_context: dict) -> dict:
    """The body the API signs for one widget turn: the browser's request keys, the host's
    capabilities and the server-built field context."""
    body = {key: payload[key] for key in ASSISTANT_KEYS if key in payload}
    if "client" in body:
        body["client"] = host_client(body["client"])
    body["field_context"] = field_context
    return body
