"""Acceptance checks for a geo and/or simulation service against the proposed v0.1 contract.

    uv run python -m agent_service.integrations.contract_check --geo https://geo.example/geo --sim https://sim.example/sim \\
        [--field demo-poltava-01] [--revision 3] [--context field_context.json] [--partial-field FIELD] \\
        [--token-env SERVICE_TOKEN] [--json report.json]

    uv run python -m agent_service.integrations.contract_check --examples     # contract-valid sample requests

Every check uses the agent's own HTTP adapter and validation boundary, so PASS means the agent can consume
the service. It says nothing about scientific quality. Outcomes are kept apart and only PASS counts as passed:

    PASS                     checked and conforms
    FAIL                     checked and does not conform (fields, expectation, category and a fix are printed)
    SKIPPED                  not applicable or cannot be forced against this service (the reason is printed)
    BLOCKED_NO_CREDENTIALS   needs a credential that was not given (e.g. authentication without --token-env)
    BLOCKED_NO_SERVICE       the service did not answer, so the checks depending on it could not run
    NOT_PERFORMED            no URL given for this service

The verdict states whether REAL integration is verified: only a service reporting mode "live" with no FAIL or
BLOCKED outcome counts. A contract stub or local fixture is never a verified teammate integration.
Credentials and response bodies are never printed. Exit code: 1 if any FAIL or BLOCKED_NO_SERVICE, else 0.
"""

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx2
from pydantic import SecretStr

from contracts.integration import INTEGRATION_VERSION, ComparisonRequest, FieldProfileRequest
from contracts.models import CompareRotationsRequest, FieldContext

from .http import HttpGeoProvider, HttpService, HttpSimulationProvider
from .normalize import check_comparison, check_profile, idempotency_key
from .ports import IntegrationFailure

FIXTURES = Path(__file__).parents[2] / "contracts" / "fixtures"
SPEC = "docs/M5_TEAM_INTEGRATION_CONTRACT.md"
ALL_SCENARIOS = ("typical", "drought_2024", "heat_plus_2_5c")

# What to change, per failure category. Kept generic on purpose: the issue list names the exact fields.
REMEDIATION = {
    "unavailable": "Service unreachable or 5xx. Check the URL (it must end at the service prefix, e.g. .../sim), that it "
                   "listens, and that /v1/capabilities answers 200 with a capabilities body.",
    "timeout": "No answer within the deadline. Answer faster, or for long computations report async_jobs: true and "
               "return status 'accepted'/'running' with a result_id, then serve GET /v1/comparisons/{id}.",
    "invalid_response": "The body does not satisfy the contract or a boundary rule. Fix the listed fields; the schema "
                        "is contracts/schema/fieldshift-integration.schema.json. Echo request_id; send "
                        "Content-Type: application/json; label every value of a stub/fixture data_status 'fixture'.",
    "unsupported_contract": f"Send contract_version \"{INTEGRATION_VERSION}\" in bodies and accept the request header "
                            f"X-FieldShift-Contract: {INTEGRATION_VERSION}.",
    "wrong_field": "The response is for another field, or its centroid is more than about 2 km from the host's field. "
                   "Return data only for the requested field_id; centroid is [lon, lat] in WGS84.",
    "wrong_revision": "Echo comparison.field_context_revision from the request in field_context_revision.",
    "not_found": "Unknown id. For GET /v1/comparisons/{id} keep results until expires_at; reply 404 with an "
                 "IntegrationError body (code not_found) for ids you do not know.",
    "expired": "expires_at must be in the future when the result is served (>= created_at + result_ttl_s).",
    "rejected": "The service refused (4xx) or redirected. Do not redirect; return an IntegrationError body with a code "
                "from the contract; check the bearer token and field authorization.",
    "too_large": "Response larger than the agent accepts (AGENT_INTEGRATION_MAX_BYTES, default 2 MB). Return a compact "
                 "profile (timeseries go through the timeseries endpoint, not the profile).",
    "unauthenticated": "The service answered without credentials. Require the server-to-server bearer token on every "
                       "endpoint and answer 401 with an IntegrationError body otherwise.",
}
BLOCKING = {"FAIL", "BLOCKED_NO_SERVICE"}


@dataclass
class Step:
    operation: str
    status: str
    summary: str = ""
    category: str | None = None
    issues: list[tuple[str, str]] = field(default_factory=list)
    remediation: str | None = None


@dataclass
class Report:
    target: dict[str, str] = field(default_factory=dict)   # service -> what it is
    modes: dict[str, str] = field(default_factory=dict)    # service -> reported mode
    steps: list[Step] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return sum(s.status in BLOCKING for s in self.steps)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.steps:
            out[s.status] = out.get(s.status, 0) + 1
        return out

    def verdict(self) -> str:
        if not self.modes:
            return "REAL INTEGRATION: NOT VERIFIED (no service answered)"
        if any(s.status in BLOCKING for s in self.steps):
            return "REAL INTEGRATION: NOT VERIFIED (failed or blocked checks)"
        not_live = [svc for svc, mode in self.modes.items() if mode != "live"]
        if not_live:
            return f"REAL INTEGRATION: NOT VERIFIED ({', '.join(not_live)} is a {self.modes[not_live[0]]}, not a teammate service)"
        blocked = [s.operation for s in self.steps if s.status == "BLOCKED_NO_CREDENTIALS"]
        if blocked:
            return "REAL INTEGRATION: PARTLY VERIFIED (live service; authentication not tested without credentials)"
        return f"REAL INTEGRATION: VERIFIED for {', '.join(self.modes)} (mode live, all applicable checks PASS)"


def _target(mode: str | None) -> str:
    return {"live": "actual external service (mode live)", "stub": "contract stub (mode stub): NOT a teammate service",
            "fixture": "local fixture (mode fixture): NOT a teammate service"}.get(mode or "", "unknown (no valid capabilities)")


def _context(field_id: str, path: str | None) -> FieldContext:
    if path:
        return FieldContext.model_validate_json(Path(path).read_text(encoding="utf-8"))
    for p in FIXTURES.glob("field_context.*.json"):
        ctx = FieldContext.model_validate_json(p.read_text(encoding="utf-8"))
        if ctx.field_id == field_id:
            return ctx
    raise SystemExit(f"no FieldContext for {field_id!r}: pass --context with the host's context for that field")


async def run(args, transport: httpx2.AsyncBaseTransport | None = None) -> Report:
    token_env = getattr(args, "token_env", None)
    token = SecretStr(os.environ[token_env]) if token_env and os.environ.get(token_env) else None
    ctx = _context(args.field, args.context)
    report = Report()

    def add(operation: str, status: str, summary: str = "", category: str | None = None,
            issues: list[tuple[str, str]] | None = None) -> None:
        report.steps.append(Step(operation, status, summary, category, issues or [],
                                 REMEDIATION.get(category or "") if status == "FAIL" else None))

    async def step(operation: str, coro) -> bool:
        try:
            result = await coro
            status, summary = result if isinstance(result, tuple) else ("PASS", result or "")
            add(operation, status, summary)
            return status == "PASS"
        except IntegrationFailure as f:
            add(operation, "FAIL", f.detail, f.category, f.issues)
        except AssertionError as e:
            add(operation, "FAIL", str(e), "invalid_response")
        return False

    def http(service: str, url: str, with_token: bool = True) -> HttpService:
        return HttpService(service, url, timeout_s=args.timeout, max_bytes=5_000_000, token=token if with_token else None,
                           transport=transport)

    async def probe(service: str, provider, operation: str) -> bool:
        """Capabilities first: an unreachable service blocks the rest instead of failing every check."""
        try:
            caps = await provider.capabilities(f"cc_{service[:3]}_caps")
        except IntegrationFailure as f:
            if f.category in ("unavailable", "timeout"):
                add(operation, "BLOCKED_NO_SERVICE", f.detail, f.category)
                report.target[service] = "no answer: " + f.category
                return False
            add(operation, "FAIL", f.detail, f.category, f.issues)
            return False
        report.target[service], report.modes[service] = _target(caps.service.mode), caps.service.mode
        problems = []
        if caps.service.service != service:
            problems.append(("service/service", f"\"{service}\""))
        if service == "geo" and not (caps.geo and caps.geo.field_profile):
            problems.append(("geo/field_profile", "true (otherwise the agent will not use this service)"))
        if service == "simulation" and not (caps.simulation and caps.simulation.compare_rotations and caps.simulation.scenarios):
            problems.append(("simulation/compare_rotations, simulation/scenarios", "true and a non-empty list"))
        if problems:
            add(operation, "FAIL", "capabilities do not offer the operation", "invalid_response", problems)
            return False
        state[service] = caps
        add(operation, "PASS", f"implementation={caps.service.implementation} (claimed); mode={caps.service.mode}")
        return True

    async def auth_check(service: str, url: str, provider_cls) -> None:
        operation = f"{service}: refuses calls without the service token"
        if token is None:
            add(operation, "BLOCKED_NO_CREDENTIALS", "authentication not tested: pass --token-env with the service token")
            return
        anonymous = provider_cls(http(service, url, with_token=False))
        try:
            await anonymous.capabilities(f"cc_{service[:3]}_anon")
            add(operation, "FAIL", "answered 200 without a token", "unauthenticated", [("Authorization", "required")])
        except IntegrationFailure as f:
            add(operation, "PASS" if f.category == "rejected" else "FAIL",
                f"refused ({f.category})", None if f.category == "rejected" else f.category)
        finally:
            await anonymous.aclose()

    state: dict = {}

    # ------------------------------------------------------------ geo
    if not args.geo:
        report.target["geo"] = "verification not performed (no --geo URL)"
        add("geo: all checks", "NOT_PERFORMED")
    else:
        geo = HttpGeoProvider(http("geo", args.geo))

        async def geo_profile():
            resp = await geo.field_profile(FieldProfileRequest(contract_version=INTEGRATION_VERSION,
                                                               request_id="cc_geo_profile", field_id=ctx.field_id))
            checked = check_profile(resp, ctx, "cc_geo_profile")
            if checked.dropped:  # provenance, unit and dataset rules (SMAP/POWER/SoilGrids, units, timestamps)
                raise IntegrationFailure("invalid_response", f"{len(checked.dropped)} item(s) dropped by boundary rules",
                                         issues=[(f"profile/{i}", why) for i, why in checked.dropped])
            return f"status={resp.status}; facts={len(checked.facts)}; layers={len(checked.layers)}; " \
                   f"timeseries={len(checked.timeseries)}; provenance and unit rules: all passed"

        async def geo_partial():
            if not args.partial_field:
                return "SKIPPED", "no --partial-field given (a field the service knows has missing datasets)"
            pctx = _context(args.partial_field, None)
            resp = await geo.field_profile(FieldProfileRequest(contract_version=INTEGRATION_VERSION,
                                                               request_id="cc_geo_partial", field_id=pctx.field_id))
            check_profile(resp, pctx, "cc_geo_partial")
            missing = [f for f in resp.profile.facts if f.provenance.data_status == "unavailable"]
            if resp.status != "partial" or not missing:
                raise IntegrationFailure("invalid_response", "missing datasets are not reported",
                                         issues=[("status", "\"partial\""), ("profile/facts", "a fact with data_status unavailable")])
            if any(f.quantity is not None or f.category is not None for f in missing):
                raise IntegrationFailure("invalid_response", "an unavailable fact carries a value",
                                         issues=[("profile/facts/*/quantity", "absent when data_status is unavailable")])
            return f"status=partial; {len(missing)} unavailable fact(s) without values"

        async def geo_unknown_field():
            try:
                await geo.field_profile(FieldProfileRequest(contract_version=INTEGRATION_VERSION,
                                                            request_id="cc_geo_unknown", field_id="cc-unknown-field-000"))
            except IntegrationFailure as f:
                if f.category in ("not_found", "rejected"):
                    if f.detail.startswith("HTTP "):
                        raise IntegrationFailure("invalid_response", f"refused, but without an IntegrationError body ({f.detail})",
                                                 issues=[("body", "{\"error\": {code, message_key, retryable}}")]) from None
                    return f"refused as {f.category} with an IntegrationError body"
                raise
            raise IntegrationFailure("invalid_response", "an unknown field_id was answered with a profile",
                                     issues=[("field_id", "404 not_found (or 403 field_not_authorized) for unknown fields")])

        if await probe("geo", geo, "geo GET /v1/capabilities"):
            await auth_check("geo", args.geo, HttpGeoProvider)
            await step("geo POST /v1/field-profile + provenance/unit/geometry rules", geo_profile())
            await step("geo partial profile (unavailable datasets have no value)", geo_partial())
            await step("geo unknown or unauthorized field -> 404/403 with error body", geo_unknown_field())
            add("geo unsupported dataset", "SKIPPED", "the v0.1 profile request has no dataset selector")
        elif report.target.get("geo", "").startswith("no answer"):
            for op in ("geo: authentication", "geo POST /v1/field-profile", "geo partial profile", "geo unknown field"):
                add(op, "BLOCKED_NO_SERVICE")
        await geo.aclose()

    # ------------------------------------------------------------ simulation
    if not args.sim:
        report.target["simulation"] = "verification not performed (no --sim URL)"
        add("simulation: all checks", "NOT_PERFORMED")
    else:
        sim = HttpSimulationProvider(http("simulation", args.sim))

        def request(rid: str, revision: int, scenario: str | None = None) -> ComparisonRequest:
            caps = state["simulation"].simulation
            body = CompareRotationsRequest(field_id=ctx.field_id, field_context_revision=revision,
                                           constraints=[c for c in ctx.constraints if c.status == "confirmed"],
                                           scenario=scenario or caps.scenarios[0])
            return ComparisonRequest(contract_version=INTEGRATION_VERSION, request_id=rid,
                                     idempotency_key=idempotency_key(body.model_dump(mode="json"), "contract-check"),
                                     comparison=body)

        async def completed(req: ComparisonRequest):
            resp = await sim.compare(req)
            caps = state["simulation"].simulation
            deadline = asyncio.get_running_loop().time() + args.timeout
            while resp.status in ("accepted", "running") and caps.async_jobs:
                if asyncio.get_running_loop().time() > deadline:
                    raise IntegrationFailure("timeout", f"still {resp.status} after {args.timeout:.0f} s")
                await asyncio.sleep(1)
                resp = await sim.result(resp.result_id, req.request_id)
            return resp, check_comparison(resp, req)

        async def sim_compare():
            resp, checked = await completed(request("cc_sim_compare", args.revision))
            if resp.status != "completed":
                raise IntegrationFailure("invalid_response", f"status {resp.status}" + (f" ({resp.failure.code})" if resp.failure else ""),
                                         issues=[("status", "completed for a field and scenario the service supports")])
            state["result_id"] = resp.result_id
            return f"result_id={resp.result_id}; engine={resp.engine.name} {resp.engine.version}; scores={len(checked.facts)}; " \
                   f"field, revision, scenario, units, provenance, expiry: all checked"

        async def sim_revision_binding():
            resp, _ = await completed(request("cc_sim_rev", args.revision + 1))  # check_comparison verifies the echo
            return f"result bound to revision {resp.field_context_revision}"

        async def sim_get():
            resp = await sim.result(state["result_id"], "cc_sim_get")
            if resp.result_id != state["result_id"]:
                raise IntegrationFailure("invalid_response", "GET returned another result", issues=[("result_id", "the id in the URL")])
            return f"status={resp.status}"

        async def sim_idempotent():
            again = await sim.compare(request("cc_sim_compare_again", args.revision))
            if again.result_id == state["result_id"]:
                return "same Idempotency-Key -> same result_id (not recomputed)"
            return "PASS", "same Idempotency-Key produced a new result_id (allowed, but duplicates computation)"

        async def sim_unsupported_scenario():
            unsupported = [s for s in ALL_SCENARIOS if s not in state["simulation"].simulation.scenarios]
            if not unsupported:
                return "SKIPPED", "the service reports every M0 scenario"
            try:
                resp = await sim.compare(request("cc_sim_unsupported", args.revision, unsupported[0]))
            except IntegrationFailure as f:
                if f.category == "rejected":
                    return f"refused {unsupported[0]} ({f.category})"
                raise
            if resp.status == "failed" and resp.failure and resp.failure.code == "unsupported_scenario":
                return f"refused {unsupported[0]} (status failed, unsupported_scenario)"
            raise IntegrationFailure("invalid_response", f"scenario {unsupported[0]} was not refused (status {resp.status})",
                                     issues=[("status/failure", "failed / unsupported_scenario, or 422")])

        async def sim_unknown():
            try:
                await sim.result("cmp_contract_check_unknown", "cc_sim_unknown")
            except IntegrationFailure as f:
                if f.category in ("not_found", "expired"):
                    return f"refused as {f.category}"
                raise
            raise IntegrationFailure("invalid_response", "an unknown result id was answered with a result",
                                     issues=[("GET /v1/comparisons/{id}", "404 not_found for unknown ids")])

        if await probe("simulation", sim, "simulation GET /v1/capabilities"):
            await auth_check("simulation", args.sim, HttpSimulationProvider)
            if await step("simulation POST /v1/comparisons + boundary checks", sim_compare()):
                await step("simulation GET /v1/comparisons/{result_id}", sim_get())
                await step("simulation POST again with the same Idempotency-Key", sim_idempotent())
            await step("simulation result bound to the requested revision", sim_revision_binding())
            await step("simulation unsupported scenario refused", sim_unsupported_scenario())
            await step("simulation unknown result id -> 404", sim_unknown())
            add("simulation expired result", "SKIPPED", "expiry cannot be forced on a running service (agent rejects expired results: tests)")
            add("simulation timeout / cancellation", "SKIPPED", "client-side behaviour, covered by the agent's tests")
        elif report.target.get("simulation", "").startswith("no answer"):
            for op in ("simulation: authentication", "simulation POST /v1/comparisons", "simulation GET result",
                       "simulation unsupported scenario", "simulation unknown result id"):
                add(op, "BLOCKED_NO_SERVICE")
        await sim.aclose()
    return report


def render(report: Report) -> str:
    lines = [f"Field Shift integration contract v{INTEGRATION_VERSION} (PROPOSED) - {SPEC}", ""]
    for service, what in report.target.items():
        lines.append(f"TARGET {service}: {what}")
    lines.append("")
    for s in report.steps:
        lines.append(f"{s.status:<23} {s.operation}" + (f"  - {s.summary}" if s.summary else ""))
        if s.status == "FAIL":
            lines.append(f"{'':24}category: {s.category}")
            for path, expected in s.issues:
                lines.append(f"{'':24}field: {path} - expected: {expected}")
            if s.remediation:
                lines.append(f"{'':24}fix: {s.remediation}")
    counts = ", ".join(f"{k} {v}" for k, v in sorted(report.counts().items()))
    lines += ["", f"OUTCOMES: {counts}", report.verdict()]
    return "\n".join(lines)


def examples() -> str:
    """Contract-valid sample requests, generated from the models (not hand-written)."""
    ctx = _context("demo-poltava-01", None)
    profile = FieldProfileRequest(contract_version=INTEGRATION_VERSION, request_id="req_example.geo", field_id=ctx.field_id)
    body = CompareRotationsRequest(field_id=ctx.field_id, field_context_revision=ctx.revision,
                                   constraints=[c for c in ctx.constraints if c.status == "confirmed"], scenario="typical")
    compare = ComparisonRequest(contract_version=INTEGRATION_VERSION, request_id="req_example.sim1",
                                idempotency_key=idempotency_key(body.model_dump(mode="json"), "principal:farmer-example"),
                                comparison=body)
    headers = {"Content-Type": "application/json", "X-Request-ID": "<request_id>",
               "X-FieldShift-Contract": INTEGRATION_VERSION, "Authorization": "Bearer <service token from config>"}
    return "\n\n".join([
        "# Headers on every call\n" + json.dumps(headers, indent=2),
        "# POST {geo}/v1/field-profile\n" + profile.model_dump_json(indent=2),
        "# POST {sim}/v1/comparisons   (+ header Idempotency-Key: <idempotency_key>)\n"
        + compare.model_dump_json(indent=2, exclude_none=True),
        "# GET {sim}/v1/comparisons/<result_id>   and   GET {geo|sim}/v1/capabilities   (no body)",
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--geo", help="geo service base URL (.../geo)")
    parser.add_argument("--sim", help="simulation service base URL (.../sim)")
    parser.add_argument("--field", default="demo-poltava-01", help="a field id the service knows")
    parser.add_argument("--revision", type=int, default=3)
    parser.add_argument("--context", help="the host's FieldContext JSON for that field (default: the M0 fixture)")
    parser.add_argument("--partial-field", help="a field the geo service knows with some datasets unavailable")
    parser.add_argument("--token-env", help="name of an env var holding the bearer token (never pass the token inline)")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json", help="also write the report as JSON to this path")
    parser.add_argument("--examples", action="store_true", help="print contract-valid sample requests and exit")
    args = parser.parse_args()
    if args.examples:
        print(examples())
        sys.exit(0)
    if not (args.geo or args.sim):
        parser.error("give --geo and/or --sim (or --examples)")
    report = asyncio.run(run(args))
    print(render(report))
    if args.json:
        Path(args.json).write_text(json.dumps({"target": report.target, "modes": report.modes, "verdict": report.verdict(),
                                               "outcomes": report.counts(), "steps": [asdict(s) for s in report.steps]},
                                              indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    sys.exit(1 if report.failed else 0)


if __name__ == "__main__":
    main()
