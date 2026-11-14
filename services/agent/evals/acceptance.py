"""M6C canonical acceptance scenario: Poltava, no irrigation, soil moisture, two rotations under drought.

    uv run python -m evals.acceptance            # writes AGENT_DATA_DIR/evals/reports/m6c_acceptance.{json,md}

What runs for real: the agent's FastAPI app (auth, attestation verification, SSE), the LLM agent loop with the
demo's scripted model (`demo_script`, the same one the browser demo uses), tool registry, grounding and
validation, the HTTP integration adapters and their boundary checks.
What is stood in for:
  * the host backend (BFF) -> the reference signer `attestation.sign` with a key generated for this run;
  * the geo and simulation services -> the CONTRACT STUB (integration_stub), in process over ASGI. Its
    values are synthetic. It is NOT a teammate service, so this is NOT a real external end-to-end test;
  * the language model -> a fixed script (no API key needed); it proposes, the server still validates.
The browser half is e2e/live.spec.ts "canonical acceptance", run against the running services.
"""

import asyncio
import json
import os
import secrets
import sys
import time
from dataclasses import asdict, dataclass, field

import httpx2
from agent_service.api import create_app as agent_app
from agent_service.attestation import sign
from agent_service.config import Settings
from agent_service.integrations.http import (
    HttpGeoProvider,
    HttpService,
    HttpSimulationProvider,
)
from agent_service.integrations.registry import IntegrationRegistry
from agent_service.llm.scripted import ScriptedProvider, call, demo_script, say
from agent_service.llm_agent import LLMAgent
from agent_service.paths import reports_directory
from agent_service.scenarios import offer_text
from contracts.resources import FIXTURES
from integration_stub.app import create_app as stub_app
from starlette.testclient import TestClient

REPORTS = reports_directory()
TEXT = "У мене немає поливу. Покажи вологість ґрунту і порівняй дві сівозміни під час посухи."
CHOICE = offer_text("typical", "uk")  # the reply button the server offers after the drought refusal


def _context(name: str) -> dict:
    return json.loads((FIXTURES / f"field_context.{name}.json").read_text(encoding="utf-8"))


def _body(field: str = "poltava", text: str = TEXT, request_id: str = "req_m6c_accept") -> dict:
    return {"request_id": request_id, "session_id": "ses_m6c_accept", "locale": "uk", "message": {"text": text},
            "history": [], "field_context": _context(field), "client": {"widget_version": "m6c-acceptance"}}


@dataclass
class Check:
    name: str
    expected: str
    observed: str
    outcome: str  # PASS / FAIL


@dataclass
class Turn:
    text: str
    field_id: str
    request_id: str = ""
    latency_ms: int = 0
    tool_calls: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    facts: list[dict] = field(default_factory=list)
    constraints_proposed: list[dict] = field(default_factory=list)
    ui_actions: list[dict] = field(default_factory=list)
    offers: list[str] = field(default_factory=list)
    answer_excerpt: str = ""


@dataclass
class Evidence:
    target: str = ("agent app + HTTP adapters (real code) -> CONTRACT STUB in process (synthetic, NOT a teammate "
                   "service); host backend = reference signer; model = demo scripted model")
    integrations: dict = field(default_factory=dict)
    turns: list[Turn] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    compatibility: dict = field(default_factory=dict)


class Rig:
    def __init__(self):
        self.signing_key = secrets.token_urlsafe(32)  # never written anywhere
        self.api_key = secrets.token_urlsafe(32)
        self.stub = stub_app()  # one stub for the whole run, so result ids persist across turns
        self.settings = Settings(environment="test", api_key=self.api_key, context_signing_key=self.signing_key,
                                 require_context_attestation=True)

    def registry(self) -> IntegrationRegistry:
        """A fresh adapter pair per turn: the agent app closes its integration clients when it shuts down."""
        transport = httpx2.ASGITransport(app=self.stub)
        svc = lambda name, url: HttpService(name, url, timeout_s=5, max_bytes=2_000_000, transport=transport)  # noqa: E731
        return IntegrationRegistry(HttpGeoProvider(svc("geo", "http://stub/geo")),
                                   HttpSimulationProvider(svc("simulation", "http://stub/sim")), geo_mode="http", sim_mode="http")

    def status(self) -> dict:
        async def probe():
            reg = self.registry()
            try:
                return await reg.status()
            finally:
                await reg.aclose()
        return asyncio.run(probe())

    def post(self, body: dict, script=demo_script, *, principal: str | None = "farmer-poltava",
             field_id: str | None = None, revision: int | None = None, now: float | None = None):
        raw = json.dumps(body, ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        if principal:
            ctx = body["field_context"]
            headers["X-FieldShift-Context-Attestation"] = sign(
                self.signing_key.encode(), principal=principal, field_id=field_id or ctx["field_id"],
                revision=ctx["revision"] if revision is None else revision, body=raw, now=now)
        agent = LLMAgent(ScriptedProvider(script), integrations=self.registry())
        started = time.perf_counter()
        with TestClient(agent_app(self.settings, lambda _s: agent)) as client:
            with client.stream("POST", "/v1/agent/stream", content=raw, headers=headers) as resp:
                text = "".join(resp.iter_text())
        latency = int((time.perf_counter() - started) * 1000)
        if resp.status_code != 200:
            return resp.status_code, json.loads(text), [], latency
        frames = [json.loads(line[6:]) for line in text.replace("\r\n", "\n").split("\n") if line.startswith("data: ")]
        return 200, frames[-1], frames, latency

    def turn(self, field: str, text: str) -> tuple[Turn, dict]:
        body = _body(field, text)
        _, last, frames, latency = self.post(body)
        msg = last.get("message", {})
        names = {f["call_id"]: f["tool"] for f in frames if f.get("event") == "tool.started"}
        t = Turn(text=text, field_id=body["field_context"]["field_id"], request_id=body["request_id"], latency_ms=latency,
                 tool_calls=[{"tool": names.get(f["call_id"]), "status": f["status"]} for f in frames if f.get("event") == "tool.finished"],
                 warnings=[w["code"] for w in msg.get("warnings", [])],
                 facts=[{"id": f["id"], "kind": f["provenance"]["kind"], "data_status": f["provenance"]["data_status"],
                         **({"scenario": f["provenance"]["method"].split("scenario=")[1].split(";")[0]}
                            if "scenario=" in (f["provenance"].get("method") or "") else {})} for f in msg.get("facts", [])],
                 constraints_proposed=[{"id": c["id"], "status": c["status"]} for c in msg.get("constraints_proposed", [])],
                 ui_actions=[{"trigger": a["trigger"], "revision": a["field_context_revision"], **a["action"]} for a in msg.get("ui_actions", [])],
                 offers=[o for b in msg.get("blocks", []) if b["type"] == "clarifying_question" for o in b["options"]],
                 answer_excerpt=next((b["markdown"] for b in msg.get("blocks", []) if b["type"] == "text"), "")[:700])
        return t, msg


def _compatibility(ev: Evidence) -> dict:
    """Status per boundary, kept apart: what ran here is never reported as a teammate integration."""
    sim_ok = any(f.get("scenario") for t in ev.turns for f in t.facts)
    return {
        "agent_api": {"verified_locally": True, "evidence": "this run (signed SSE turns) + pytest"},
        "geo_provider": {"verified_against_contract_stub": ev.integrations.get("geo", {}).get("connected", False),
                         "verified_against_teammate_service": False, "blocked": "no_service"},
        "simulation_provider": {"verified_against_contract_stub": sim_ok,
                                "verified_against_teammate_service": False, "blocked": "no_service"},
        "host_action_adapter": {"verified_locally": "demo host only (Playwright e2e)",
                                "verified_against_teammate_service": False, "blocked": "no_service (real host app)"},
        "anthropic_provider": {"verified_live": False,
                               "blocked": "no_credentials" if not os.environ.get("ANTHROPIC_API_KEY") else "not_tested_here (run evals.run --smoke)"},
        "fao_rag": {"verified_locally": "not tested in this run (evals.run covers the lexical index)"},
    }


def run() -> Evidence:
    rig = Rig()
    ev = Evidence()
    checks = ev.checks

    def check(name: str, expected: str, observed: str, ok: bool) -> None:
        checks.append(Check(name, expected, observed, "PASS" if ok else "FAIL"))

    ev.integrations = {k: {kk: vv for kk, vv in v.items() if kk != "transport"} if isinstance(v, dict) else v
                       for k, v in rig.status().items()}
    check("geo profile via HTTP adapter", "geo connected, service_mode stub",
          json.dumps({k: ev.integrations["geo"].get(k) for k in ("connected", "service_mode")}),
          ev.integrations["geo"].get("connected") and ev.integrations["geo"].get("service_mode") == "stub")

    # ---- turn 1: the canonical request on Poltava (irrigation already confirmed there)
    t1, m1 = rig.turn("poltava", TEXT)
    ev.turns.append(t1)
    text1 = t1.answer_excerpt
    check("turn 1 completes", "message", "message" if m1 else "no message", bool(m1))
    check("constraint not duplicated", "irrigation already confirmed -> no proposal", json.dumps(t1.constraints_proposed),
          t1.constraints_proposed == [] and "вже підтверджено" in text1)
    kinds = {(f["kind"], f["data_status"]) for f in t1.facts}
    check("water facts keep provenance", "satellite product + ML estimate, both labelled demo; ML not an observation",
          json.dumps(sorted(kinds)), ("satellite_product", "fixture") in kinds and ("ml_estimate", "fixture") in kinds
          and "неперевірена оцінка" in text1 and "fixture_data" in t1.warnings)
    sims = [c for c in t1.tool_calls if c["tool"] == "compare_rotations"]
    # The refusal is an explained answer, not a failure: the farmer's step shows it as done (tools.EXPLAINED_REFUSALS);
    # what proves the refusal is that nothing was computed, shown or offered.
    check("drought refused, nothing else run", "one compare_rotations (refused); no simulation values; no panel chip",
          f"calls={json.dumps(sims)}, sim facts={sum('scenario' in f for f in t1.facts)}",
          len(sims) == 1 and sims[0]["status"] == "ok" and not any("scenario" in f for f in t1.facts)
          and not any(a["type"] == "panel.open" for a in t1.ui_actions))
    check("unavailable scenario explained", "answer names the drought scenario as not computed",
          "said" if "посуха" in text1 and "не розраховано" in text1 else "not said",
          "посуха" in text1 and "не розраховано" in text1)
    check("alternative offered, not run", f"server offers «{CHOICE}» as a reply button", json.dumps(t1.offers, ensure_ascii=False),
          CHOICE in t1.offers)
    layers = [a for a in t1.ui_actions if a["type"] == "map.set_layers"]
    check("safe map action", "SMAP layer, auto, revision 3", json.dumps(layers),
          len(layers) == 1 and layers[0]["layer_ids"] == ["l_smap_rootzone"] and layers[0]["revision"] == 3)
    no_live = not any(w in text1.lower() for w in ("live", "реальному часі"))
    check("no live claim", "no live / real-time wording", "clean" if no_live else "claim found", no_live)

    # ---- turn 2: the farmer chooses the offered alternative scenario
    t2, m2 = rig.turn("poltava", CHOICE)
    ev.turns.append(t2)
    scen = {f.get("scenario") for f in t2.facts if f["id"].startswith("sim.")}
    panel = [a for a in t2.ui_actions if a["type"] == "panel.open"]
    check("chosen scenario runs", "typical comparison computed; every value scenario=typical",
          f"scenarios={sorted(s for s in scen if s)}", scen == {"typical"})
    check("panel action names its scenario", "panel.open chip with ref_id and scenario typical", json.dumps(panel),
          len(panel) == 1 and panel[0]["trigger"] == "chip" and panel[0].get("scenario") == "typical")
    check("synthetic labelled", "fixture values, fixture_data warning, 'вигадані для перевірки' in text",
          f"warnings={t2.warnings}", "fixture_data" in t2.warnings and "вигадані для перевірки" in t2.answer_excerpt
          and all(f["data_status"] == "fixture" for f in t2.facts))
    check("no drought wording next to typical values", "answer of turn 2 never names drought",
          "clean" if "посух" not in t2.answer_excerpt.lower() else "drought named", "посух" not in t2.answer_excerpt.lower())
    result_id = panel[0]["ref_id"] if panel else "cmp_none"

    # ---- turn 3: same request on Mykolaiv (no irrigation constraint, no moisture layer)
    t3, _ = rig.turn("mykolaiv", TEXT)
    ev.turns.append(t3)
    check("constraint proposed, not saved", "k_irrigation_available proposed (status proposed)", json.dumps(t3.constraints_proposed),
          t3.constraints_proposed == [{"id": "k_irrigation_available", "status": "proposed"}])
    check("no map action without a layer", "no map.set_layers; text says the map is unchanged", json.dumps(t3.ui_actions),
          not t3.ui_actions and "карту не змінюю" in t3.answer_excerpt)

    # ---- authorization and state validation
    def refused(name: str, expected: str, **kw) -> None:
        status, data, _, _ = rig.post(_body(), **kw)
        detail = data.get("error", {}).get("detail", "") if status != 200 else "accepted"
        check(name, f"HTTP 403 ({expected})", f"HTTP {status}: {detail}", status == 403 and expected in detail)

    refused("no host attestation (direct client)", "only accepts requests signed", principal=None)
    refused("attestation for another field (unauthorized field)", "field_mismatch", field_id="demo-kansas-01")
    refused("mismatched field revision", "revision_mismatch", revision=2)
    refused("expired attestation (replay)", "expired", now=time.time() - 3600)

    def turn_with(script, field: str = "poltava", faults: dict | None = None, text: str = TEXT):
        rig.stub.state.faults = faults or {}
        try:
            return rig.post(_body(field, text, request_id="req_m6c_neg"), script)[1].get("message", {})
        finally:
            rig.stub.state.faults = {}

    def open_panel(ref: str):
        return [call(("propose_ui_actions", {"actions": [{"type": "panel.open", "panel": "comparison", "trigger": "chip",
                                                          "ref_id": ref, "reason": "", "label": "Відкрити порівняння"}]})),
                say("Порівняння відкрити не можна.")]

    m = turn_with(open_panel("cmp_forged_0000"))
    check("invalid comparison id", "panel.open with an invented id is dropped", f"ui_actions={len(m.get('ui_actions', []))}",
          m.get("ui_actions") == [])
    m = turn_with(open_panel(result_id), field="mykolaiv")
    check("late result after field switch", "the Poltava result cannot be opened in a Mykolaiv turn",
          f"ui_actions={len(m.get('ui_actions', []))}", m.get("ui_actions") == [])
    substitute = [call(("compare_rotations", {"scenario": "typical", "rotation_ids": []})), say("Порівняння немає.")]
    m = turn_with(substitute)
    check("model substitutes typical for drought", "refused (scenario_not_requested): no simulation values",
          f"sim facts={sum(f['id'].startswith('sim.') for f in m.get('facts', []))}",
          bool(m) and not any(f["id"].startswith("sim.") for f in m.get("facts", [])))
    for fault, name in (("unavailable", "simulation service unavailable"), ("expired", "expired simulation result"),
                        ("wrong_scenario", "provider returns another scenario")):
        m = turn_with(demo_script, faults={"sim": fault}, text=CHOICE)
        sims = [f for f in m.get("facts", []) if f["id"].startswith("sim.")]
        check(name, "turn completes, no simulation values, no comparison panel",
              f"sim facts={len(sims)}, panel actions={sum(a['action']['type'] == 'panel.open' for a in m.get('ui_actions', []))}",
              bool(m) and not sims and not any(a["action"]["type"] == "panel.open" for a in m.get("ui_actions", [])))
    ev.compatibility = _compatibility(ev)
    return ev


def render(ev: Evidence) -> str:
    passed = sum(c.outcome == "PASS" for c in ev.checks)
    lines = ["# M6C canonical acceptance scenario (Level A)", "",
             "Generated by `uv run python -m evals.acceptance`. **Not a real external end-to-end test:** the geo and "
             "simulation services are the contract stub (synthetic values), the host backend is the reference signer "
             "and the model is the demo's scripted model. Real teammate services: not available (Level B pending).", "",
             f"- Target: {ev.target}", f"- Integrations: `{json.dumps(ev.integrations, ensure_ascii=False)}`", ""]
    for i, t in enumerate(ev.turns, 1):
        lines += [f"## Turn {i}: {t.field_id}", "", f"- Farmer: «{t.text}»", f"- Request id: `{t.request_id}`; latency {t.latency_ms} ms",
                  f"- Tool calls: {', '.join(f'{c['tool']}={c['status']}' for c in t.tool_calls)}",
                  f"- Warnings: {', '.join(t.warnings) or 'none'}",
                  f"- Facts: {', '.join(f'{f['id']} ({f['kind']}, {f['data_status']}' + (f', scenario {f['scenario']}' if 'scenario' in f else '') + ')' for f in t.facts) or 'none'}",
                  f"- Constraints proposed: `{json.dumps(t.constraints_proposed)}`",
                  f"- UI actions: `{json.dumps(t.ui_actions, ensure_ascii=False)}`",
                  f"- Offered replies: {json.dumps(t.offers, ensure_ascii=False)}", "",
                  "```text", t.answer_excerpt, "```", ""]
    lines += [f"## Checks: {passed}/{len(ev.checks)} PASS", "", "| Check | Expected | Observed | Outcome |", "|---|---|---|---|"]
    lines += [f"| {c.name} | {c.expected} | {c.observed.replace('|', '/')} | {c.outcome} |" for c in ev.checks]
    lines += ["", "## Compatibility status", "", "```json", json.dumps(ev.compatibility, indent=2), "```"]
    return "\n".join(lines) + "\n"


def main() -> None:
    ev = run()
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "m6c_acceptance.json").write_text(json.dumps(asdict(ev), ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8", newline="\n")
    (REPORTS / "m6c_acceptance.md").write_text(render(ev), encoding="utf-8", newline="\n")
    for c in ev.checks:
        print(f"{c.outcome}  {c.name}")
    failed = [c for c in ev.checks if c.outcome != "PASS"]
    print(f"{len(ev.checks) - len(failed)}/{len(ev.checks)} PASS -> {REPORTS / 'm6c_acceptance.md'}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
