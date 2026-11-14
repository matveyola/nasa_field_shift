"""A contract-conformant stub of the geo (/geo) and simulation (/sim) services.

It serves the M0 synthetic fixtures over the proposed v0.1 HTTP contract and labels itself
`mode: "stub"`. It proves the HTTP transport, the envelopes and the agent's checks work; it does
NOT prove that member 1's or member 2's service is connected. Faults can be injected for tests.
With `sim=` (python -m integration_stub --sim-marsden URL) /sim is a real service instead: the Marsden rotation API
behind the contract (agent_service/integrations/marsden.py), labelled `mode: "live"`; /geo stays the synthetic stub.
"""

import asyncio
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import ValidationError

from agent_service.integrations.fixture import FixtureGeoProvider, FixtureSimulationProvider
from agent_service.integrations.ports import IntegrationFailure
from contracts.integration import INTEGRATION_VERSION, ComparisonRequest, FieldProfileRequest

FAULTS = {
    "unavailable",        # 503 with an IntegrationError body
    "malformed",          # 200 with a body that is not JSON
    "schema",             # 200 with JSON that breaks the contract
    "old_contract",       # contract_version "0.0"
    "wrong_field",        # profile/result for another field
    "wrong_revision",     # result for another revision
    "wrong_scenario",     # a valid result for another scenario than requested (M6C)
    "mislabel_live",      # stub data labelled as live observations
    "expired",            # result already expired
    "swapped_centroid",   # centroid as [lat, lon]
    "redirect",           # 307 to an internal address
    "oversize",           # a body larger than the client's limit
    "slow",               # never answers within a normal deadline
}


def _error(status: int, code: str, key: str, retryable: bool, request_id: str | None = None) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message_key": key, "retryable": retryable}}
    if request_id:
        body["error"]["request_id"] = request_id
    return JSONResponse(body, status_code=status)


def _failure(f: IntegrationFailure, request_id: str | None) -> JSONResponse:
    if f.category == "not_found":
        return _error(404, "not_found", "error.not_found", False, request_id)
    if f.category == "expired":
        return _error(410, "expired", "error.result_expired", False, request_id)
    return _error(503, "unavailable", "error.unavailable", True, request_id)


def create_app(faults: dict[str, str] | None = None, delay_s: float = 0.0, token: str | None = None,
               sim: Any = None) -> FastAPI:
    """`faults` maps "geo" / "sim" to one of FAULTS. Tests may change `app.state.faults` at run time.
    `token`: when set, every call must carry `Authorization: Bearer <token>` (401 otherwise), so the acceptance
    checker's authentication check can be exercised. Real services must enforce their own credentials."""
    app = FastAPI(title="Field Shift contract stub (geo + simulation, v0.1 proposed)", docs_url="/docs")
    app.state.faults = dict(faults or {})
    geo, sim = FixtureGeoProvider(mode="stub"), sim or FixtureSimulationProvider(mode="stub")
    app.state.sim = sim

    async def before(service: str, request: Request, data_call: bool = True) -> Response | None:
        if delay_s:
            await asyncio.sleep(delay_s)
        if token is not None and not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {token}"):
            return _error(401, "invalid_request", "error.unauthorized", False)
        fault = app.state.faults.get(service)
        if request.headers.get("x-fieldshift-contract", INTEGRATION_VERSION) != INTEGRATION_VERSION:
            return _error(400, "unsupported_contract", "error.unsupported_contract", False)
        if fault == "unavailable":
            return _error(503, "unavailable", "error.unavailable", True)
        if fault == "malformed":
            return Response("<html>oops</html>", media_type="application/json")
        if fault == "redirect":
            return RedirectResponse("http://169.254.169.254/latest/meta-data/", status_code=307)
        if fault == "oversize":
            return Response(b"[" + b"0," * 3_000_000 + b"0]", media_type="application/json")
        if fault == "slow" and data_call:  # the capability probe still answers: the computation hangs
            await asyncio.sleep(3600)
        return None

    def tamper(service: str, body: dict) -> dict:
        fault = app.state.faults.get(service)
        if fault == "schema":
            body.pop("service", None)
        elif fault == "old_contract":
            body["contract_version"] = "0.0"
        elif fault == "wrong_field":
            if "profile" in body:
                body["profile"]["field_id"] = "demo-kansas-01"
            elif "field_id" in body:
                body["field_id"] = "demo-kansas-01"
        elif fault == "wrong_revision" and "field_context_revision" in body:
            body["field_context_revision"] += 1
        elif fault == "wrong_scenario" and "scenario" in body:
            body["scenario"] = "heat_plus_2_5c" if body["scenario"] != "heat_plus_2_5c" else "typical"
        elif fault == "mislabel_live":
            text = json.dumps(body).replace('"data_status": "fixture"', '"data_status": "live"')
            body = json.loads(text)
        elif fault == "expired" and "expires_at" in body:
            past = datetime.now(UTC) - timedelta(minutes=1)
            body["created_at"] = (past - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
            body["expires_at"] = past.isoformat().replace("+00:00", "Z")
        elif fault == "swapped_centroid" and "centroid" in body:
            body["centroid"] = list(reversed(body["centroid"]))
        return body

    @app.get("/geo/v1/capabilities")
    async def geo_caps(request: Request):
        if early := await before("geo", request, data_call=False):
            return early
        caps = await geo.capabilities(request.headers.get("x-request-id", "cap"))
        return tamper("geo", caps.model_dump(mode="json", exclude_none=True))

    @app.post("/geo/v1/field-profile")
    async def field_profile(request: Request):
        if early := await before("geo", request):
            return early
        try:
            req = FieldProfileRequest.model_validate_json(await request.body())
        except ValidationError:
            return _error(422, "invalid_request", "error.invalid_request", False)
        try:
            resp = await geo.field_profile(req)
        except IntegrationFailure as f:
            return _failure(f, req.request_id)
        return tamper("geo", resp.model_dump(mode="json", exclude_none=True))

    @app.get("/sim/v1/capabilities")
    async def sim_caps(request: Request):
        if early := await before("sim", request, data_call=False):
            return early
        try:
            caps = await sim.capabilities(request.headers.get("x-request-id", "cap"))
        except IntegrationFailure as f:
            return _failure(f, None)
        return tamper("sim", caps.model_dump(mode="json", exclude_none=True))

    @app.post("/sim/v1/comparisons")
    async def compare(request: Request):
        if early := await before("sim", request):
            return early
        try:
            req = ComparisonRequest.model_validate_json(await request.body())
        except ValidationError:
            return _error(422, "invalid_request", "error.invalid_request", False)
        if request.headers.get("idempotency-key") not in (None, req.idempotency_key):
            return _error(422, "invalid_request", "error.idempotency_key_mismatch", False, req.request_id)
        try:
            resp = await sim.compare(req)
        except IntegrationFailure as f:
            return _failure(f, req.request_id)
        return tamper("sim", resp.model_dump(mode="json", exclude_none=True))

    @app.get("/sim/v1/comparisons/{result_id}")
    async def result(result_id: str, request: Request):
        if early := await before("sim", request):
            return early
        request_id = request.headers.get("x-request-id", "get")
        try:
            resp = await sim.result(result_id, request_id)
        except IntegrationFailure as f:
            return _failure(f, request_id)
        return tamper("sim", resp.model_dump(mode="json", exclude_none=True))

    return app
