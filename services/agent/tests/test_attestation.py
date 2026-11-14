"""M6B: per-request context attestation, field authorization gate, principal isolation, source identity.

The "host backend" here is the reference signer `attestation.sign` with a test key: a controlled
trusted-server fixture. These tests verify the agent's side of the boundary. They do NOT verify the real
host backend's authentication or authorization, which does not exist yet.
"""

import asyncio
import json
import shutil
import socket
import subprocess
import threading
import time

import pytest
import uvicorn
from pydantic import ValidationError

from agent_service.attestation import AttestationError, sign, verify
from agent_service.config import Settings
from agent_service.dev_agent import DeterministicDevAgent
from agent_service.integrations.fixture import FixtureSimulationProvider
from agent_service.integrations.http import HttpService, HttpSimulationProvider
from agent_service.integrations.ports import IntegrationFailure, UnavailableProvider
from agent_service.integrations.registry import IntegrationRegistry, transport_facts
from agent_service.llm.scripted import ScriptedProvider, demo_script, say
from agent_service.llm_agent import LLMAgent
from integration_stub.app import create_app

from .conftest import make_client, parse_sse, request_body
from .test_trust_boundary import FORGED, HONEST, LIVE_CLAIM, READ, LiveGeo, geo_registry

KEY = "context-signing-key-for-tests-only-0123456789"
API_KEY = "a" * 40


def settings(require: bool = True, **over) -> Settings:
    return Settings(environment="test", api_key=API_KEY, context_signing_key=KEY, require_context_attestation=require, **over)


def body_with_forged(field: str = "kansas", text: str = "What is my soil moisture?") -> dict:
    body = request_body(field, text)
    body["field_context"]["facts"].append(json.loads(json.dumps(FORGED)))
    body["locale"] = "en"
    return body


def post(agent, body: dict, *, cfg: Settings | None = None, token: str | None = None, raw: bytes | None = None,
         headers: dict | None = None):
    data = raw if raw is not None else json.dumps(body, ensure_ascii=False).encode()
    h = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}", **(headers or {})}
    if token:
        h["X-FieldShift-Context-Attestation"] = token
    with make_client(cfg or settings(), agent=agent) as client:
        with client.stream("POST", "/v1/agent/stream", content=data, headers=h) as resp:
            text = "".join(resp.iter_text())
            if resp.status_code != 200:
                return resp.status_code, json.loads(text)
            return 200, parse_sse(text)[-1][2]


def signed(body: dict, *, principal: str = "farmer-7", key: str = KEY, revision: int | None = None, now: float | None = None,
           lifetime_s: int = 120) -> tuple[bytes, str]:
    raw = json.dumps(body, ensure_ascii=False).encode()
    ctx = body["field_context"]
    token = sign(key.encode(), principal=principal, field_id=ctx["field_id"],
                 revision=ctx["revision"] if revision is None else revision, body=raw, now=now, lifetime_s=lifetime_s)
    return raw, token


def llm(*steps):
    return LLMAgent(ScriptedProvider(list(steps)))


# ------------------------------------------------------------------ 1-4, 8: what is NOT trust


def test_1_direct_client_with_fabricated_live_nasa_data_is_unverified():
    status, data = post(DeterministicDevAgent(), body_with_forged(), cfg=settings(require=False))
    line = next(line for line in data["message"]["blocks"][0]["markdown"].split("\n") if "0.48" in line)
    assert status == 200 and "not confirmed by the data service" in line


def test_2_and_4_a_valid_api_key_is_not_field_authorization_or_provenance():
    # The transport is authenticated (bearer key) but nothing about the data is: refused when attestation is required…
    status, data = post(DeterministicDevAgent(), body_with_forged())
    assert status == 403 and data["error"]["message_key"] == "error.field_not_authorized"
    # …and, where it is not required, the forged live value stays unverified and cannot back a live claim.
    status, data = post(llm(READ, say(LIVE_CLAIM), say(LIVE_CLAIM)), body_with_forged(), cfg=settings(require=False))
    assert "Live satellite" not in data["message"]["blocks"][0]["markdown"]


def test_3_values_swapped_after_signing_are_refused():
    body = request_body("kansas", "What is my soil moisture?")
    raw, token = signed(body)
    tampered = json.loads(raw)
    tampered["field_context"]["facts"][0]["quantity"]["value"] = 99.0  # another field's (or any) value
    status, data = post(DeterministicDevAgent(), tampered, token=token)
    assert status == 403 and data["error"]["detail"] == "Context attestation refused: body_mismatch."


def test_3b_a_token_for_one_field_cannot_carry_another_field():
    _, token = signed(request_body("poltava", "hi"))
    status, data = post(DeterministicDevAgent(), request_body("kansas", "hi"), token=token)
    assert status == 403


def test_7_revision_is_bound():
    body = request_body("poltava", "hi")
    raw, token = signed(body, revision=body["field_context"]["revision"] - 1)  # host signed an older revision
    status, data = post(DeterministicDevAgent(), body, raw=raw, token=token)
    assert status == 403 and data["error"]["detail"].endswith("revision_mismatch.")


@pytest.mark.parametrize("headers", [
    {"X-FieldShift-Principal": "bff"}, {"X-Forwarded-User": "admin"}, {"X-FieldShift-Trusted": "true"},
    {"X-FieldShift-Context-Attestation": "v1.e30.AAAA"},
])
def test_8_impersonation_through_untrusted_metadata_is_refused(headers):
    status, data = post(DeterministicDevAgent(), body_with_forged(), headers=headers)
    assert status == 403


@pytest.mark.parametrize("make_token, category", [
    (lambda raw, b: sign(b"wrong-key-wrong-key-wrong-key-0000", principal="p", field_id=b["field_context"]["field_id"],
                         revision=b["field_context"]["revision"], body=raw), "bad_signature"),
    (lambda raw, b: sign(KEY.encode(), principal="p", field_id=b["field_context"]["field_id"],
                         revision=b["field_context"]["revision"], body=raw, now=time.time() - 3600), "expired"),
    (lambda raw, b: sign(KEY.encode(), principal="p", field_id=b["field_context"]["field_id"],
                         revision=b["field_context"]["revision"], body=raw, lifetime_s=86400), "bad_lifetime"),
    (lambda raw, b: sign(KEY.encode(), principal="bad principal!", field_id=b["field_context"]["field_id"],
                         revision=b["field_context"]["revision"], body=raw), "bad_principal"),
    (lambda raw, b: "v2.abc.def", "malformed"),
])
def test_forged_or_stale_attestations_are_refused_with_a_safe_category(make_token, category):
    body = request_body("kansas", "hi")
    raw = json.dumps(body).encode()
    status, data = post(DeterministicDevAgent(), body, raw=raw, token=make_token(raw, body))
    assert status == 403 and data["error"]["detail"] == f"Context attestation refused: {category}."
    assert "v1." not in json.dumps(data)  # the token is never echoed


# ------------------------------------------------------------------ what a valid attestation does (and does not)


def test_a_valid_attestation_makes_the_hosts_facts_attested():
    body = body_with_forged()
    raw, token = signed(body)
    status, data = post(llm(READ, say(HONEST)), body, raw=raw, token=token)
    md = data["message"]["blocks"][0]["markdown"]
    assert status == 200 and "0.48" in md and "not confirmed" not in md


def test_5_a_trusted_geo_service_still_wins_over_attested_context():
    """The host is accountable for what it signs; a configured data service is the source and still wins."""
    body = body_with_forged()
    raw, token = signed(body)
    agent = LLMAgent(ScriptedProvider([READ, say(HONEST)]), integrations=geo_registry(LiveGeo(value=0.19)))
    status, data = post(agent, body, raw=raw, token=token)
    assert status == 200 and "0.19" in data["message"]["blocks"][0]["markdown"]


def test_6_a_fixture_service_cannot_claim_live_provenance():
    app = create_app(faults={"sim": "mislabel_live"})
    import httpx2
    sim = HttpSimulationProvider(HttpService("simulation", "http://stub/sim", timeout_s=2, max_bytes=10**6,
                                             transport=httpx2.ASGITransport(app=app)))
    registry = IntegrationRegistry(UnavailableProvider("geo"), sim, geo_mode="off", sim_mode="http")
    status, data = post(LLMAgent(ScriptedProvider(demo_script), integrations=registry),
                        request_body("poltava", "Compare crop rotations for my field."), cfg=settings(require=False))
    assert status == 200 and not [f for f in data["message"]["facts"] if f["id"].startswith("sim.")]


def test_a_signed_request_is_single_use_on_every_endpoint():
    """M6C: replaying a captured signed request must not rerun the agent turn (model calls, service calls)."""
    provider = ScriptedProvider(demo_script)
    body = request_body("poltava", "Що відомо про моє поле?")
    raw, token = signed(body)
    h = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}", "X-FieldShift-Context-Attestation": token}
    with make_client(settings(), agent=LLMAgent(provider)) as client:
        assert client.post("/v1/agent/stream", content=raw, headers=h).status_code == 200
        calls = len(provider.requests)
        for path in ("/v1/agent/stream", "/v1/agent/message"):
            replay = client.post(path, content=raw, headers=h)
            assert replay.status_code == 403 and "replayed" in replay.json()["error"]["detail"]
        assert len(provider.requests) == calls  # no further model call
        _, token2 = signed(body)  # the host re-signs a retry of the same bytes: a new token (jti) is accepted
        assert token2 != token
        assert client.post("/v1/agent/stream", content=raw, headers={**h, "X-FieldShift-Context-Attestation": token2}).status_code == 200


def test_the_replay_memory_forgets_tokens_that_can_no_longer_be_valid(monkeypatch):
    from agent_service import api
    seen: dict[str, float] = {}
    clock = [1000.0]
    monkeypatch.setattr(api.time, "monotonic", lambda: clock[0])
    assert api._first_use(seen, "t1") and not api._first_use(seen, "t1")
    clock[0] += api._SEEN_TTL_S + 1
    assert api._first_use(seen, "t2") and len(seen) == 1  # t1 purged: it expired long ago anyway


# ------------------------------------------------------------------ idempotency and principal isolation


def compare_as(sim, principal: str | None, session: str, cfg: Settings | None = None) -> int:
    registry = IntegrationRegistry(UnavailableProvider("geo"), sim, geo_mode="off", sim_mode="fixture")
    body = request_body("poltava", "Compare crop rotations for my field.", session_id=session)
    if principal:
        raw, token = signed(body, principal=principal)
        status, _ = post(LLMAgent(ScriptedProvider(demo_script), integrations=registry), body, raw=raw, token=token,
                         cfg=cfg or settings())
    else:
        status, _ = post(LLMAgent(ScriptedProvider(demo_script), integrations=registry), body, cfg=cfg or settings(require=False))
    return status


def test_two_users_with_identical_requests_never_share_a_result():
    sim = FixtureSimulationProvider()
    assert compare_as(sim, "farmer-a", "ses_shared") == 200 and compare_as(sim, "farmer-b", "ses_shared") == 200
    assert len(sim.results) == 2  # same session id, different verified principals: separate computations


def test_one_user_two_sessions_and_retries_de_duplicate():
    sim = FixtureSimulationProvider()
    for session in ("ses_tab_1", "ses_tab_2", "ses_tab_2"):  # second tab, then a retry
        assert compare_as(sim, "farmer-a", session) == 200
    assert len(sim.results) == 1


def test_an_attacker_reusing_a_session_id_gets_nothing_where_attestation_is_required():
    sim = FixtureSimulationProvider()
    assert compare_as(sim, "farmer-a", "ses_victim") == 200
    assert compare_as(sim, None, "ses_victim", cfg=settings(require=True)) == 403
    assert len(sim.results) == 1


def test_a_new_revision_is_a_new_computation():
    sim = FixtureSimulationProvider()
    registry = IntegrationRegistry(UnavailableProvider("geo"), sim, geo_mode="off", sim_mode="fixture")
    for revision in (3, 4):
        body = request_body("poltava", "Compare crop rotations for my field.")
        body["field_context"]["revision"] = revision
        raw, token = signed(body)
        assert post(LLMAgent(ScriptedProvider(demo_script), integrations=registry), body, raw=raw, token=token)[0] == 200
    assert {r.field_context_revision for r in sim.results.values()} == {3, 4}


# ------------------------------------------------------------------ configuration


def test_the_removed_global_flag_fails_loudly():
    with pytest.raises(ValueError, match="AGENT_CONTEXT_ATTESTED was removed"):
        Settings.from_env({"AGENT_CONTEXT_ATTESTED": "true"})
    with pytest.raises(ValidationError):
        Settings(context_attested=True)


@pytest.mark.parametrize("kwargs, message", [
    ({"context_signing_key": "short"}, "at least 32"),
    ({"context_signing_key": API_KEY, "api_key": API_KEY}, "must differ"),
    ({"require_context_attestation": True}, "needs AGENT_CONTEXT_SIGNING_KEY"),
    ({"environment": "production", "api_key": API_KEY, "cors_origins": ["https://app.example"]}, "production requires"),
])
def test_attestation_settings_are_validated(kwargs, message):
    with pytest.raises(ValidationError, match=message):
        Settings(**kwargs)


def test_verify_is_independent_of_the_http_layer():
    raw = b'{"x":1}'
    token = sign(KEY.encode(), principal="p", field_id="f1", revision=2, body=raw)
    assert verify(token, KEY.encode(), body=raw, field_id="f1", revision=2).principal == "p"
    with pytest.raises(AttestationError):
        verify(token, KEY.encode(), body=raw + b" ", field_id="f1", revision=2)


# ------------------------------------------------------------------ source identity


def test_transport_facts_keep_endpoint_transport_and_claims_apart():
    assert transport_facts("https://sim.example/sim", True) == {
        "endpoint": "https://sim.example", "server_authenticated_by": "TLS certificate (system trust store)",
        "agent_authenticates_with": "bearer token"}
    assert transport_facts("http://127.0.0.1:8100/sim", False)["server_authenticated_by"].startswith("nothing")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not on PATH: cannot create a throwaway certificate")
def test_an_https_endpoint_with_an_untrusted_certificate_is_refused(tmp_path):
    """The response would claim to be the simulation service; TLS says otherwise, and TLS wins."""
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
                    "-days", "1", "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"], check=True, capture_output=True)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning",
                                           ssl_certfile=str(cert), ssl_keyfile=str(key)))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        assert server.started
        # Control: a client that explicitly trusts this certificate reaches the very same endpoint…
        import ssl

        import httpx2
        trusted = ssl.create_default_context(cafile=str(cert))

        async def reachable() -> int:
            async with httpx2.AsyncClient(verify=trusted) as client:
                return (await client.get(f"https://127.0.0.1:{port}/sim/v1/capabilities")).status_code
        assert asyncio.run(reachable()) == 200
        # …while the agent's adapter, using the system trust store, refuses it: only certificate trust differs.
        sim = HttpSimulationProvider(HttpService("simulation", f"https://127.0.0.1:{port}/sim", timeout_s=5, max_bytes=10**6))
        with pytest.raises(IntegrationFailure) as e:
            asyncio.run(sim.capabilities("req.tls"))
        assert e.value.category == "unavailable"
    finally:
        server.should_exit = True
