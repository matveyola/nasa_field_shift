"""M5: geo and simulation integration — contracts, validation boundary, adapters, tool, readiness, races."""

import asyncio
import json
import socket
import threading
import time
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
import uvicorn
from pydantic import ValidationError

from agent_service.agent import TurnEvidence
from agent_service.config import Settings
from agent_service.grounding import check, render_fact
from agent_service.integrations.fixture import FixtureGeoProvider, FixtureSimulationProvider
from agent_service.integrations.http import HttpGeoProvider, HttpService, HttpSimulationProvider
from agent_service.integrations.normalize import ComparisonRecord, check_comparison, check_profile, idempotency_key
from agent_service.integrations.ports import IntegrationFailure, UnavailableProvider
from agent_service.integrations.registry import IntegrationRegistry
from agent_service.llm.scripted import ScriptedProvider, call, demo_script, say
from agent_service.llm_agent import LLMAgent
from agent_service.service import AgentService, ui_action_problem
from contracts.integration import (
    ComparisonRequest, ComparisonResponse, FieldProfileRequest, FieldProfileResponse,
)
from contracts.models import CompareRotationsRequest, FieldContext, UIActionEnvelope
from integration_stub.app import create_app

from .conftest import as_request, context, make_client, parse_sse, request_body

POLTAVA = FieldContext.model_validate_json(json.dumps(context("poltava")))


def profile_request(field_id: str = "demo-poltava-01", rid: str = "req.geo") -> FieldProfileRequest:
    return FieldProfileRequest(contract_version="0.1", request_id=rid, field_id=field_id)


def comparison_request(field_id: str = "demo-poltava-01", revision: int = 3, scenario: str = "typical",
                       rotation_ids: list[str] | None = None, constraints=()) -> ComparisonRequest:
    body = CompareRotationsRequest(field_id=field_id, field_context_revision=revision, scenario=scenario,
                                   rotation_ids=rotation_ids or [], constraints=list(constraints))
    return ComparisonRequest(contract_version="0.1", request_id="req.sim1",
                             idempotency_key=idempotency_key(body.model_dump(mode="json"), "test-session"), comparison=body)


def stub_providers(faults: dict | None = None, timeout_s: float = 2.0):
    app = create_app(faults=faults or {})
    transport = httpx2.ASGITransport(app=app)
    geo = HttpGeoProvider(HttpService("geo", "http://stub/geo", timeout_s=timeout_s, max_bytes=1_000_000, transport=transport))
    sim = HttpSimulationProvider(HttpService("simulation", "http://stub/sim", timeout_s=timeout_s, max_bytes=1_000_000,
                                             transport=transport))
    return app, geo, sim


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ contracts


def test_integration_envelopes_reject_inconsistent_status():
    good = run(FixtureSimulationProvider().compare(comparison_request()))
    data = json.loads(good.model_dump_json())
    data["status"] = "running"  # results with a non-completed status
    with pytest.raises(ValidationError, match="only a completed comparison"):
        ComparisonResponse.model_validate_json(json.dumps(data))
    data = json.loads(good.model_dump_json())
    data["comparison"]["rotations"][0]["scores"]["water_risk"]["value"] = float("nan")
    with pytest.raises(ValidationError):
        ComparisonResponse.model_validate_json(json.dumps(data).replace("NaN", "NaN"))
    data = json.loads(good.model_dump_json())
    data["comparison"]["rotations"][0]["scores"]["water_risk"]["provenance"]["data_status"] = "guessed"
    with pytest.raises(ValidationError):  # unknown data_status
        ComparisonResponse.model_validate_json(json.dumps(data))


def test_m0_schema_is_untouched_by_m5():
    from contracts.export import SCHEMA_PATH, build
    assert SCHEMA_PATH.read_text(encoding="utf-8") == build()
    assert "ComparisonResponse" not in SCHEMA_PATH.read_text(encoding="utf-8")


# ------------------------------------------------------------------ fixture and unavailable providers


def test_fixture_profile_is_synthetic_and_verbatim():
    resp = run(FixtureGeoProvider().field_profile(profile_request()))
    assert resp.service.mode == "fixture"
    assert all(f.provenance.data_status in ("fixture", "unavailable") for f in resp.profile.facts)
    assert resp.profile.facts == POLTAVA.facts  # provenance, uncertainty and timestamps preserved exactly
    checked = check_profile(resp, POLTAVA, "req.geo")
    assert checked.facts == POLTAVA.facts and checked.dropped == []


def test_fixture_simulation_covers_only_what_it_has():
    sim = FixtureSimulationProvider()
    ok = run(sim.compare(comparison_request()))
    assert ok.status == "completed" and ok.service.mode == "fixture"
    assert run(sim.compare(comparison_request(scenario="drought_2024"))).status == "failed"   # no invented scenario
    assert run(sim.compare(comparison_request(field_id="demo-kansas-01"))).failure.code == "insufficient_data"
    again = run(sim.compare(comparison_request()))  # same idempotency key: same result, nothing recomputed
    assert again.result_id == ok.result_id
    assert run(sim.result(ok.result_id, "req.get")).status == "completed"
    with pytest.raises(IntegrationFailure) as e:
        run(sim.result("cmp_unknown", "req.get"))
    assert e.value.category == "not_found"


def test_unavailable_provider_never_returns_data():
    for method, args in (("field_profile", (profile_request(),)), ("compare", (comparison_request(),)),
                         ("capabilities", ("r",))):
        with pytest.raises(IntegrationFailure) as e:
            run(getattr(UnavailableProvider("simulation"), method)(*args))
        assert e.value.category == "unavailable"


# ------------------------------------------------------------------ the validation boundary


def _profile_with(mutate) -> FieldProfileResponse:
    resp = run(FixtureGeoProvider().field_profile(profile_request()))
    data = json.loads(resp.model_dump_json())
    mutate(data)
    return FieldProfileResponse.model_validate_json(json.dumps(data))


def _fact(data, fid):
    return next(f for f in data["profile"]["facts"] if f["id"] == fid)


@pytest.mark.parametrize("fid, mutate, reason", [
    ("f_sm_rootzone_2025_07", lambda f: f["provenance"].__setitem__("spatial", {"resolution_m": 30}), "9 km"),
    ("f_ph_0_30", lambda f: f["provenance"]["source"].__setitem__("provider", "NASA LP DAAC"), "not NASA"),
    ("f_sm_rootzone_2025_07", lambda f: f["quantity"].__setitem__("unit", "%"), "not converted"),
    ("f_sm_rootzone_2025_07", lambda f: f["provenance"].__setitem__("retrieved_at", "2099-01-01T00:00:00Z"), "future"),
])
def test_scientific_rules_drop_misdescribed_facts(fid, mutate, reason):
    checked = check_profile(_profile_with(lambda d: mutate(_fact(d, fid))), POLTAVA, "req.geo")
    assert fid not in {f.id for f in checked.facts}
    assert any(i == fid and reason in r for i, r in checked.dropped)
    assert len(checked.facts) == len(POLTAVA.facts) - 1  # the rest is kept


def test_nasa_power_cannot_claim_field_scale_resolution():
    kansas = FieldContext.model_validate_json(json.dumps(context("kansas")))
    resp = run(FixtureGeoProvider().field_profile(profile_request("demo-kansas-01")))
    data = json.loads(resp.model_dump_json())
    power = next(f for f in data["profile"]["facts"] if "POWER" in (f["provenance"].get("source") or {}).get("provider", ""))
    power["provenance"]["spatial"] = {"resolution_m": 1000}
    checked = check_profile(FieldProfileResponse.model_validate_json(json.dumps(data)), kansas, "req.geo")
    assert power["id"] in {i for i, _ in checked.dropped}


def test_zero_is_a_value_and_missing_is_not():
    def zero(d):
        _fact(d, "f_clay_0_30")["quantity"]["value"] = 0
    checked = check_profile(_profile_with(zero), POLTAVA, "req.geo")
    assert next(f for f in checked.facts if f.id == "f_clay_0_30").quantity.value == 0
    mykolaiv = FieldContext.model_validate_json(json.dumps(context("mykolaiv")))
    resp = run(FixtureGeoProvider().field_profile(profile_request("demo-mykolaiv-01")))
    unavailable = [f for f in check_profile(resp, mykolaiv, "req.geo").facts if f.provenance.data_status == "unavailable"]
    assert unavailable and all(f.quantity is None and f.category is None for f in unavailable)
    assert resp.status == "partial"


@pytest.mark.parametrize("mutate, category", [
    (lambda d: d["profile"].__setitem__("field_id", "demo-kansas-01"), "wrong_field"),
    (lambda d: d.__setitem__("centroid", [-98.0, 38.5]), "wrong_field"),
    (lambda d: d.__setitem__("centroid", list(reversed(d["centroid"]))), "invalid_response"),
    (lambda d: d.__setitem__("request_id", "someone.else"), "invalid_response"),
    (lambda d: _fact(d, "f_ph_0_30")["provenance"].__setitem__("data_status", "live"), "invalid_response"),
])
def test_whole_profile_rejections(mutate, category):
    with pytest.raises(IntegrationFailure) as e:
        check_profile(_profile_with(mutate), POLTAVA, "req.geo")
    assert e.value.category == category


@pytest.mark.parametrize("mutate, category", [
    (lambda d: d.__setitem__("field_id", "demo-kansas-01"), "wrong_field"),
    (lambda d: d.__setitem__("field_context_revision", 99), "wrong_revision"),
    (lambda d: d["comparison"]["rotations"][0]["scores"]["n_input"].__setitem__("unit", "t/ha"), "invalid_response"),
])
def test_comparison_rejections(mutate, category):
    req = comparison_request()
    data = json.loads(run(FixtureSimulationProvider().compare(req)).model_dump_json())
    mutate(data)
    with pytest.raises(IntegrationFailure) as e:
        check_comparison(ComparisonResponse.model_validate_json(json.dumps(data)), req)
    assert e.value.category == category


def test_expired_comparison_is_rejected():
    req = comparison_request()
    resp = run(FixtureSimulationProvider(ttl_s=60).compare(req))
    with pytest.raises(IntegrationFailure) as e:
        check_comparison(resp, req, now=datetime.now(UTC) + timedelta(minutes=2))
    assert e.value.category == "expired"


def test_simulation_facts_keep_value_unit_uncertainty_and_context():
    req = comparison_request()
    checked = check_comparison(run(FixtureSimulationProvider().compare(req)), req)
    yield_risk = checked.facts[checked.score_refs["rot_adaptive"]["yield_risk"]]
    assert (yield_risk.quantity.value, yield_risk.quantity.unit) == (15, "%")
    assert (yield_risk.quantity.uncertainty.low, yield_risk.quantity.uncertainty.high) == (9, 23)
    assert yield_risk.provenance.kind == "ml_estimate" and yield_risk.provenance.data_status == "fixture"
    assert "scenario=typical" in yield_risk.provenance.method and checked.record.result_id in yield_risk.provenance.method
    assert checked.record.rotation_ids == ("rot_adaptive", "rot_conventional")


# ------------------------------------------------------------------ HTTP adapter


def test_http_adapter_round_trip_through_the_stub():
    _, geo, sim = stub_providers()
    caps = run(sim.capabilities("req.cap"))
    assert caps.service.mode == "stub" and caps.simulation.scenarios == ["typical"]
    resp = run(geo.field_profile(profile_request()))
    assert check_profile(resp, POLTAVA, "req.geo").facts == POLTAVA.facts
    cmp = run(sim.compare(comparison_request()))
    assert check_comparison(cmp, comparison_request()).record is not None
    assert run(sim.result(cmp.result_id, "req.get")).result_id == cmp.result_id


@pytest.mark.parametrize("fault, category", [
    ("malformed", "invalid_response"), ("schema", "invalid_response"), ("old_contract", "unsupported_contract"),
    ("redirect", "rejected"), ("oversize", "too_large"), ("unavailable", "unavailable"),
])
def test_http_adapter_turns_bad_responses_into_failures(fault, category):
    _, geo, _ = stub_providers({"geo": fault})
    with pytest.raises(IntegrationFailure) as e:
        run(geo.field_profile(profile_request()))
    assert e.value.category == category
    assert "169.254" not in e.value.detail  # never echoes where a redirect pointed


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_http_adapter_over_a_real_socket():
    """Proves the transport (TCP, headers, JSON). It does not prove a teammate's service is connected."""
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(faults={"sim": "slow"}), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        geo = HttpGeoProvider(HttpService("geo", f"http://127.0.0.1:{port}/geo", timeout_s=5, max_bytes=1_000_000))
        sim = HttpSimulationProvider(HttpService("simulation", f"http://127.0.0.1:{port}/sim", timeout_s=0.5, max_bytes=1_000_000))
        assert run(geo.field_profile(profile_request())).service.mode == "stub"
        with pytest.raises(IntegrationFailure) as e:  # the stub's computation hangs: a real socket read timeout
            run(sim.compare(comparison_request()))
        assert e.value.category == "timeout"
        dead = HttpGeoProvider(HttpService("geo", f"http://127.0.0.1:{_free_port()}/geo", timeout_s=2, max_bytes=1000))
        with pytest.raises(IntegrationFailure) as e:
            run(dead.field_profile(profile_request()))
        # Nothing listens there. Windows retries a refused connect for ~2 s, so it can surface as a timeout.
        assert e.value.category in ("unavailable", "timeout") and e.value.retryable
    finally:
        server.should_exit = True
        thread.join(5)


def test_http_adapter_sends_contract_headers_and_never_the_token_in_logs(caplog):
    seen = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.update(request.headers)
        return httpx2.Response(503, json={"error": {"code": "unavailable", "message_key": "error.unavailable", "retryable": True}})

    from pydantic import SecretStr
    sim = HttpSimulationProvider(HttpService("simulation", "https://sim.internal", timeout_s=1, max_bytes=1000,
                                             token=SecretStr("s3cr3t-token"), transport=httpx2.MockTransport(handler)))
    with caplog.at_level("INFO"), pytest.raises(IntegrationFailure):
        run(sim.compare(comparison_request()))
    assert seen["x-fieldshift-contract"] == "0.1" and seen["idempotency-key"].startswith("idem_")
    assert seen["authorization"] == "Bearer s3cr3t-token"
    assert "s3cr3t" not in caplog.text and "integration service=simulation op=compare" in caplog.text


# ------------------------------------------------------------------ registry, settings, readiness


def test_registry_status_distinguishes_off_fixture_stub_and_down():
    _, geo, sim = stub_providers()
    status = run(IntegrationRegistry(geo, sim, geo_mode="http", sim_mode="http").status())
    assert status["simulation"]["service_mode"] == "stub" and status["demo_mode"] and status["live_services"] == []
    _, geo, sim = stub_providers({"sim": "unavailable"})
    down = run(IntegrationRegistry(geo, sim, geo_mode="http", sim_mode="http").status())
    assert down["simulation"]["connected"] is False and down["degraded"] is True
    off = run(IntegrationRegistry(UnavailableProvider("geo"), UnavailableProvider("simulation"), geo_mode="off", sim_mode="off").status())
    assert off["simulation"] == {"configured": "off", "connected": False} and not off["degraded"]


@pytest.mark.parametrize("kwargs, message", [
    ({"sim_provider": "http"}, "needs AGENT_SIM_URL"),
    ({"sim_provider": "http", "sim_url": "file:///etc/passwd"}, "needs AGENT_SIM_URL"),
    ({"environment": "production", "api_key": "x" * 40, "cors_origins": ["https://app.example"],
      "context_signing_key": "s" * 40, "require_context_attestation": True, "geo_provider": "fixture"},
     "not allowed in production"),
    ({"environment": "production", "api_key": "x" * 40, "cors_origins": ["https://app.example"],
      "context_signing_key": "s" * 40, "require_context_attestation": True, "sim_provider": "http",
      "sim_url": "http://sim.internal"}, "requires https"),
])
def test_unsafe_integration_settings_are_refused(kwargs, message):
    with pytest.raises(ValidationError, match=message):
        Settings(**kwargs)


def test_readiness_reports_integrations_separately():
    s = Settings(environment="test", agent="scripted-llm", sim_provider="fixture", rag_mode="off")
    with make_client(s) as client:
        body = client.get("/health/ready").json()
    assert body["status"] == "ready"
    assert body["integrations"]["simulation"]["service_mode"] == "fixture"
    assert body["integrations"]["geo"] == {"configured": "off", "connected": False}
    assert body["integrations"]["demo_mode"] is True


# ------------------------------------------------------------------ result references (host-facing safety)


def _record(**over) -> ComparisonRecord:
    base = dict(result_id="cmp_real", field_id="demo-poltava-01", field_context_revision=3, scenario="typical",
                rotation_ids=("rot_adaptive",), expires_at=datetime.now(UTC) + timedelta(hours=1), mode="fixture")
    return ComparisonRecord(**(base | over))


@pytest.mark.parametrize("action, record, ok", [
    ({"type": "panel.open", "panel": "comparison", "ref_id": "cmp_real"}, _record(), True),
    ({"type": "panel.open", "panel": "comparison", "ref_id": "cmp_other"}, _record(), False),
    ({"type": "panel.open", "panel": "comparison", "ref_id": "cmp_real"}, _record(field_id="demo-kansas-01"), False),
    ({"type": "panel.open", "panel": "comparison", "ref_id": "cmp_real"}, _record(field_context_revision=2), False),
    ({"type": "panel.open", "panel": "comparison", "ref_id": "cmp_real"},
     _record(expires_at=datetime.now(UTC) - timedelta(seconds=1)), False),
    ({"type": "compare.select_rotations", "rotation_ids": ["rot_adaptive"]}, _record(), True),
    ({"type": "compare.select_rotations", "rotation_ids": ["rot_magic"]}, _record(), False),
    ({"type": "compare.select_rotations", "rotation_ids": ["rot_adaptive"]}, None, False),
])
def test_result_references_must_point_at_a_live_result_of_this_field(action, record, ok):
    envelope = UIActionEnvelope.model_validate_json(json.dumps(
        {"id": "a1", "field_context_revision": 3, "trigger": "chip", "label": "x", "action": action}))
    results = {record.result_id: record} if record else {}
    assert (ui_action_problem(envelope, POLTAVA, results) is None) == ok


def test_service_rejects_a_fact_that_is_not_from_context_or_evidence():
    """The final check: a simulation-looking fact without verified evidence is a contract violation."""
    class Forger:
        name = "forger"

        async def respond(self, request, turn_id):
            from contracts.models import AgentMessage
            req = comparison_request()
            fact = next(iter(check_comparison(await FixtureSimulationProvider().compare(req), req).facts.values()))
            yield AgentMessage.model_validate_json(json.dumps({
                "turn_id": turn_id, "locale": "en", "blocks": [{"type": "text", "markdown": "x"}],
                "facts": [json.loads(fact.model_dump_json())],
                "meta": {"provider": "x", "model_id": "x", "prompt_version": "x", "latency_ms": 0}}))

    async def go():
        return [e async for e in AgentService(Forger(), 10).stream(as_request(request_body("poltava", "hi")))]
    assert run(go())[-1].event == "error"


# ------------------------------------------------------------------ agent turns (scenarios A, D, E, H, I)


def _agent(script, registry: IntegrationRegistry | None, **kw) -> LLMAgent:
    return LLMAgent(ScriptedProvider(script), integrations=registry, **kw)


def _fixture_registry(geo=True, sim=True) -> IntegrationRegistry:
    return IntegrationRegistry(FixtureGeoProvider() if geo else UnavailableProvider("geo"),
                               FixtureSimulationProvider() if sim else UnavailableProvider("simulation"),
                               geo_mode="fixture" if geo else "off", sim_mode="fixture" if sim else "off")


def _turn(agent, field="poltava", text="Compare crop rotations for my field.", **overrides):
    with make_client(agent=agent) as client:
        with client.stream("POST", "/v1/agent/stream", json=request_body(field, text, **overrides)) as resp:
            return parse_sse("".join(resp.iter_text()))


def test_scenario_a_profile_through_the_integration_layer_stays_synthetic():
    frames = _turn(_agent(demo_script, _fixture_registry(sim=False)), text="What is known about my field?")
    message = frames[-1][2]["message"]
    assert message["facts"] and all(f["provenance"]["data_status"] in ("fixture", "unavailable") for f in message["facts"])
    assert {w["code"] for w in message["warnings"]} >= {"fixture_data"}


def test_scenario_d_comparison_is_explained_with_result_references_and_labels():
    frames = _turn(_agent(demo_script, _fixture_registry()))
    message = frames[-1][2]["message"]
    sim_facts = [f for f in message["facts"] if f["id"].startswith("sim.")]
    assert len(sim_facts) == 8 and all(f["provenance"]["data_status"] == "fixture" for f in sim_facts)
    [action] = message["ui_actions"]
    assert action["action"]["panel"] == "comparison" and action["trigger"] == "chip"
    assert action["action"]["ref_id"] in sim_facts[0]["provenance"]["method"]
    md = message["blocks"][0]["markdown"]
    assert "rotation calculation, case “typical conditions”" in md and "made up for testing" in md and "15% (9–23)" in md


def test_scenario_e_no_simulation_service_no_tool_no_numbers():
    provider = ScriptedProvider([say("The rotation simulation is not connected, so I cannot compare rotations.")])
    frames = _turn(LLMAgent(provider, integrations=_fixture_registry(sim=False)))
    assert "compare_rotations" not in {t.name for t in provider.requests[0]["tools"]}
    context_sent = provider.requests[0]["transcript"][0].text
    assert '"rotation_simulation"' in context_sent and '"available": false' in context_sent
    assert not [f for f in frames[-1][2]["message"]["facts"] if f["domain"] == "rotation"]


def test_scenario_h_three_kinds_of_numbers_stay_distinguishable():
    req = comparison_request()
    sim_fact = next(iter(check_comparison(run(FixtureSimulationProvider().compare(req)), req).facts.values()))
    smap = next(f for f in POLTAVA.facts if f.id == "f_sm_rootzone_2025_07")
    downscaled = next(f for f in POLTAVA.facts if f.provenance.kind == "ml_estimate")
    rendered = {name: render_fact(f, "en", mark_demo=False) for name, f in (("smap", smap), ("ml", downscaled), ("sim", sim_fact))}
    assert rendered["smap"].endswith("**")  # plain satellite data: no note (source and period in "data used")
    assert "unchecked estimate" in rendered["ml"] and "estimate" not in rendered["smap"]
    assert "rotation calculation, case “typical conditions”" in rendered["sim"]
    assert len(set(rendered.values())) == 3
    # next to real values, each demo value says so
    assert all("demo" in render_fact(f, "en", mark_demo=True) for f in (smap, downscaled, sim_fact))
    # and the prose cannot turn any computed value into an observation
    retrieved = {f.id: f for f in (smap, downscaled, sim_fact)}
    g = check("The model measured {{fact:" + sim_fact.id + "}} here.", retrieved=retrieved, known_ids=set(retrieved),
              farmer_text="", simulation_ran=True, any_live_fact=False, lang="en")
    assert "estimate_as_observation" in {v.category for v in g.violations}
    ok = check("The model estimates {{fact:" + sim_fact.id + "}}; it is not measured.", retrieved=retrieved,
               known_ids=set(retrieved), farmer_text="", simulation_ran=True, any_live_fact=False, lang="en")
    assert ok.violations == []


class _HangingSim(FixtureSimulationProvider):
    def __init__(self):
        super().__init__()
        self.started, self.cancelled = asyncio.Event(), False

    async def compare(self, request):
        self.started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


@pytest.mark.anyio
async def test_scenario_i_cancellation_reaches_the_external_call():
    sim = _HangingSim()
    registry = IntegrationRegistry(UnavailableProvider("geo"), sim, geo_mode="off", sim_mode="fixture")
    agent = _agent([call(("compare_rotations", {"scenario": "typical", "rotation_ids": []}))], registry)
    events = []

    async def consume():
        async for e in AgentService(agent, 60).stream(as_request(request_body("poltava", "compare"))):
            events.append(e.event)

    task = asyncio.create_task(consume())
    await asyncio.wait_for(sim.started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sim.cancelled  # the service call itself was cancelled, not left running
    assert events == ["turn.started", "tool.started"]  # no result, no terminal event after cancellation


@pytest.mark.anyio
async def test_concurrent_turns_on_two_fields_never_mix_results():
    agent = _agent(demo_script, _fixture_registry(geo=False))
    service = AgentService(agent, 30)

    async def one(field):
        return [e async for e in service.stream(as_request(request_body(field, "Compare crop rotations for my field.")))][-1]

    poltava, kansas = await asyncio.gather(one("poltava"), one("kansas"))
    p_ids = {f.id for f in poltava.message.facts}
    assert p_ids and all(i.startswith("sim.") or not i.startswith("sim") for i in p_ids)
    assert not [f for f in kansas.message.facts if f.id.startswith("sim.")]  # Kansas has no comparison: nothing borrowed
    assert not kansas.message.ui_actions
    assert {a.action.ref_id for a in poltava.message.ui_actions} <= {
        m for f in poltava.message.facts for m in [f.provenance.method.split("result=")[-1].split(";")[0]]}


def test_only_confirmed_constraints_reach_the_simulation():
    seen = []

    class Spy(FixtureSimulationProvider):
        async def compare(self, request):
            seen.append(request)
            return await super().compare(request)

    registry = IntegrationRegistry(UnavailableProvider("geo"), Spy(), geo_mode="off", sim_mode="fixture")
    _turn(_agent(demo_script, registry))
    [request] = seen
    statuses = {c.status for c in request.comparison.constraints}
    assert statuses <= {"confirmed"}  # Poltava's proposed fertilizer constraint is not simulated
    assert [c.type for c in request.comparison.constraints] == ["irrigation_available"]


def test_compare_tool_cannot_be_steered_to_another_field_or_url():
    from agent_service.tools import COMPARE_SPEC
    assert set(COMPARE_SPEC.input_schema["properties"]) == {"scenario", "rotation_ids"}
    provider = ScriptedProvider([
        call(("compare_rotations", {"scenario": "typical", "rotation_ids": [], "field_id": "demo-kansas-01",
                                    "base_url": "http://169.254.169.254/"})),
        say("I can only compare rotations for the selected field."),
    ])
    frames = _turn(LLMAgent(provider, integrations=_fixture_registry(geo=False)))
    result = json.loads(provider.requests[1]["transcript"][-1].results[0].content)
    assert result["error"]["category"] == "invalid_arguments"
    assert not [f for f in frames[-1][2]["message"]["facts"] if f["id"].startswith("sim.")]


def test_turn_evidence_is_never_streamed():
    frames = _turn(_agent(demo_script, _fixture_registry()))
    assert {name for name, _, _ in frames} <= {"turn.started", "tool.started", "tool.finished", "text.delta", "turn.completed"}
    assert TurnEvidence.__name__ not in json.dumps([d for _, _, d in frames])


@pytest.mark.anyio
async def test_the_first_capabilities_read_asks_the_service_even_right_after_boot(monkeypatch):
    """Regression (CI, 2026-11-12): time.monotonic() counts from boot; on a runner up for less than the status TTL
    the never-filled cache looked fresh, so compare_rotations was not offered."""
    from agent_service.integrations import registry as reg

    monkeypatch.setattr(reg.time, "monotonic", lambda: 1.0)
    integrations = IntegrationRegistry(UnavailableProvider("geo"), FixtureSimulationProvider(), geo_mode="off",
                                       sim_mode="fixture")
    assert await integrations.simulation_available()
