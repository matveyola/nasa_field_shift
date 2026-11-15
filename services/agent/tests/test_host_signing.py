"""The Field Shift API's real signer against this agent's real verifier (offline, no model).

The host module apps/api/src/field_shift/modules/agent/signing.py is standard-library only, so it is loaded here by
path: a change to how the host builds or signs a turn now fails the agent's own tests too.
"""

import importlib.util
import json
from pathlib import Path

from agent_service.attestation import HEADER, verify
from agent_service.config import Settings

from .conftest import context, make_client, request_body

HOST_SIGNING = Path(__file__).resolve().parents[3] / "apps/api/src/field_shift/modules/agent/signing.py"
_spec = importlib.util.spec_from_file_location("field_shift_host_signing", HOST_SIGNING)
host = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(host)

KEY, SECRET = "k" * 40, "s" * 40
SETTINGS = Settings(environment="production", cors_origins=[], agent="scripted-llm", rag_mode="off",
                    api_key=KEY, context_signing_key=SECRET, require_context_attestation=True)


def host_turn(principal: str = host.LOCAL_PRINCIPAL) -> tuple[bytes, str, dict]:
    """What RemoteAgentProvider.stream sends: the widget's request, the server-built context, the host's signature."""
    server_ctx = context("poltava")
    widget = request_body(context("poltava"), "Що відомо про моє поле?", locale="uk")
    widget["field_context"]["facts"] = []  # the browser's context is discarded anyway
    widget["client"] = {"widget_version": "w", "capabilities": {
        "actions": ["map.set_layers", "compare.set_constraints"], "screens": [{"screen_id": "admin", "label": "x"}]}}
    body = host.assistant_body(widget, server_ctx)
    raw = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    token = host.attestation(SECRET.encode(), principal=principal, field_id=server_ctx["field_id"],
                             revision=server_ctx["revision"], body=raw)
    return raw, token, body


def post(client, raw: bytes, token: str):
    return client.post("/v1/agent/message", content=raw, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {KEY}", HEADER: token})


def test_a_host_signed_widget_turn_is_accepted_and_carries_only_host_capabilities():
    raw, token, body = host_turn()
    assert body["client"]["capabilities"] == {"actions": ["map.set_layers"], "screens": []}
    assert body["field_context"]["facts"]  # the server's facts, not the browser's empty list
    with make_client(SETTINGS) as client:
        response = post(client, raw, token)
    assert response.status_code == 200, response.text
    assert response.json()["meta"]["provider"] == "scripted-llm"


def test_a_replayed_or_altered_host_turn_is_refused():
    raw, token, _ = host_turn()
    with make_client(SETTINGS) as client:
        assert post(client, raw, token).status_code == 200
        assert post(client, raw, token).status_code == 403  # single use, unchanged
        altered = raw.replace("Що відомо".encode(), "Що відомо, 37 мм".encode())
        assert post(client, altered, host_turn()[1]).status_code == 403  # body hash no longer matches


def test_each_signed_in_member_gets_their_own_isolation_scope():
    scopes = set()
    for user in (7, 8):
        raw, token, body = host_turn(host.agent_principal(user, 1))
        attested = verify(token, SECRET.encode(), body=raw, field_id=body["field_context"]["field_id"],
                          revision=body["field_context"]["revision"])
        scopes.add(attested.isolation_scope)
        with make_client(SETTINGS) as client:
            assert post(client, raw, token).status_code == 200
    assert scopes == {"principal:farm-1:user-7", "principal:farm-1:user-8"}
    raw, token, body = host_turn()
    local = verify(token, SECRET.encode(), body=raw, field_id=body["field_context"]["field_id"],
                   revision=body["field_context"]["revision"])
    assert local.isolation_scope == "principal:field-shift-local-farm"  # single-farm mode: one shared scope
