"""The installed agent verifier consumes a deterministic test-only host wire vector.

API tests use their installed signer to reproduce these exact bytes/tokens. Neither
service imports the sibling source tree. Frozen clock/nonce values are test inputs,
not provider evidence or production authentication tokens.
"""

import json
from pathlib import Path

import pytest
from agent_service import attestation
from agent_service.attestation import HEADER, verify
from agent_service.config import Settings

from .conftest import make_client

VECTOR = json.loads(
    (Path(__file__).parent / "fixtures/host_signed_turn.json").read_text()
)


@pytest.fixture(autouse=True)
def vector_clock(monkeypatch):
    monkeypatch.setattr(attestation.time, "time", lambda: VECTOR["now"])


KEY, SECRET = "k" * 40, "s" * 40
SETTINGS = Settings(
    environment="production",
    cors_origins=[],
    agent="scripted-llm",
    rag_mode="off",
    api_key=KEY,
    context_signing_key=SECRET,
    require_context_attestation=True,
)


def host_turn(principal: str = "field-shift-local-farm") -> tuple[bytes, str, dict]:
    vector = next(
        item for item in VECTOR["tokens"].values() if item["principal"] == principal
    )
    raw = VECTOR["signed_body"].encode()
    return raw, vector["token"], json.loads(raw)


def post(client, raw: bytes, token: str):
    return client.post(
        "/v1/agent/message",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {KEY}",
            HEADER: token,
        },
    )


def test_a_host_signed_widget_turn_is_accepted_and_carries_only_host_capabilities():
    raw, token, body = host_turn()
    assert body["client"]["capabilities"] == {
        "actions": ["map.set_layers"],
        "screens": [],
    }
    assert body["field_context"][
        "facts"
    ]  # the server's facts, not the browser's empty list
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
        assert (
            post(client, altered, host_turn()[1]).status_code == 403
        )  # body hash no longer matches


def test_each_signed_in_member_gets_their_own_isolation_scope():
    scopes = set()
    for user in (7, 8):
        raw, token, body = host_turn(f"farm-1:user-{user}")
        attested = verify(
            token,
            SECRET.encode(),
            body=raw,
            field_id=body["field_context"]["field_id"],
            revision=body["field_context"]["revision"],
        )
        scopes.add(attested.isolation_scope)
        with make_client(SETTINGS) as client:
            assert post(client, raw, token).status_code == 200
    assert scopes == {"principal:farm-1:user-7", "principal:farm-1:user-8"}
    raw, token, body = host_turn()
    local = verify(
        token,
        SECRET.encode(),
        body=raw,
        field_id=body["field_context"]["field_id"],
        revision=body["field_context"]["revision"],
    )
    assert (
        local.isolation_scope == "principal:field-shift-local-farm"
    )  # single-farm mode: one shared scope
