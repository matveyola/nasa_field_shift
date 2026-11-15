"""Installed host signer must emit the shared wire vector consumed by agent tests."""

import json
from pathlib import Path

import pytest

from field_shift.modules.agent import signing

VECTOR = json.loads(
    (
        Path(__file__).resolve().parents[3] / "services/agent/tests/fixtures/host_signed_turn.json"
    ).read_text()
)


@pytest.mark.parametrize("name", ["local", "member7", "member8"])
def test_installed_signer_matches_cross_service_body_and_token(name, monkeypatch):
    vector = VECTOR["tokens"][name]
    principal = (
        signing.LOCAL_PRINCIPAL if name == "local" else signing.agent_principal(int(name[-1]), 1)
    )
    assert principal == vector["principal"]
    monkeypatch.setattr(signing, "time", lambda: VECTOR["now"])
    monkeypatch.setattr(signing.secrets, "token_urlsafe", lambda length: vector["nonce"])
    body = signing.assistant_body(VECTOR["widget"], VECTOR["context"])
    raw = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    assert raw.decode() == VECTOR["signed_body"]
    assert (
        signing.attestation(
            VECTOR["key"].encode(),
            principal=principal,
            field_id=VECTOR["context"]["field_id"],
            revision=VECTOR["context"]["revision"],
            body=raw,
        )
        == vector["token"]
    )
