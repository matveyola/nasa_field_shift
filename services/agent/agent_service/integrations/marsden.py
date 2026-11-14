"""The Marsden rotation service (a teammate's API, received 2026-11-12) behind the team's v0.1 simulation contract.

Marsden (`POST /api/rotations/assessment`) replays recent archive weather (ERA5-Seamless via Open-Meteo, 2017-2026)
for its own fields in Iowa and computes, per rotation and scenario, the share of crop water demand not met (FAO-56
water balance) and the harvest lost to that shortage (FAO-33 yield response). This adapter speaks the contract on
its behalf and keeps only what Marsden computes, labelled the way Marsden labels it:

- water_shortfall -> water_risk: deterministic_calc ("formula"); the method says it is the share of water need not met;
- modeled_yield_loss_due_to_water -> yield_loss_water: ml_estimate ("model"), never validated on the field;
- yield_risk and fertilizer are null in Marsden today: they stay absent, never zero. Values it adds later are mapped;
- scenarios: baseline -> typical, drought -> driest_recent_year, heat -> hottest_recent_year: each is the season Marsden
  picked from that field's archive (the year goes into the method). No other scenario is offered or substituted;
- fields: only host field ids mapped to a Marsden field id; any other field gets a failed result (insufficient_data);
- farmer constraints are not used by Marsden (it assumes no irrigation): said in every result's warnings.
Results stay in memory until expires_at, for GET /comparisons/{id}. Marsden's own `accuracy` and
`validation_error_pct_points` are passed on as warnings: it reports no error with a sample size, so nothing is
turned into a Validation.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx2

from contracts.integration import (
    INTEGRATION_VERSION,
    CapabilitiesResponse,
    ComparisonFailure,
    ComparisonRequest,
    ComparisonResponse,
    EngineInfo,
    IntegrationWarning,
    ServiceInfo,
    SimulationCapabilities,
)
from contracts.models import (
    ModelInfo,
    Provenance,
    RotationComparison,
    RotationOption,
    RotationScores,
    RotationStep,
    Score,
    Temporal,
)

from .ports import IntegrationFailure, Span

IMPLEMENTATION = "fieldshift-marsden-bridge 0.1"
SCENARIOS = {"typical": "baseline", "driest_recent_year": "drought", "hottest_recent_year": "heat"}  # ours -> Marsden
CROPS = {"maize_grain": "maize"}  # Marsden crop ids -> the team's crop ids (others pass through)
TTL = timedelta(hours=1)
WORDS = {"baseline": "typical season", "drought": "driest season", "heat": "hottest season"}


class MarsdenSimulationProvider:
    mode = "http"

    def __init__(self, base_url: str, fields: dict[str, str], *, timeout_s: float = 30.0,
                 transport: httpx2.AsyncBaseTransport | None = None):
        self.fields = dict(fields)  # host field_id -> Marsden field_id
        # ponytail: no response size cap (Marsden answers ~30 kB); add one if it is ever exposed beyond localhost.
        self.client = httpx2.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s, follow_redirects=False,
                                         transport=transport)
        self.version = "unknown"
        self.rotation_ids: list[str] = []  # from Marsden's catalog
        self.results: dict[str, ComparisonResponse] = {}

    async def _json(self, method: str, path: str, request_id: str, operation: str, body: Any = None) -> Any:
        span = Span("simulation", f"marsden_{operation}", request_id)
        try:
            try:
                r = await self.client.request(method, path, json=body)
            except httpx2.TimeoutException:
                raise IntegrationFailure("timeout", "Marsden timed out", retryable=True) from None
            except httpx2.TransportError as e:
                raise IntegrationFailure("unavailable", f"Marsden unreachable ({type(e).__name__})", retryable=True) from None
            if r.status_code >= 300:
                category = "not_found" if r.status_code == 404 else "unavailable" if r.status_code >= 500 else "rejected"
                raise IntegrationFailure(category, f"Marsden HTTP {r.status_code}", retryable=r.status_code >= 500)
            try:
                data = r.json()
            except ValueError:
                raise IntegrationFailure("invalid_response", "Marsden answered with a body that is not JSON") from None
        except IntegrationFailure as f:
            span.end("error", failure=f.category)
            raise
        span.end("ok", mode="live")
        return data

    def _info(self) -> ServiceInfo:
        return ServiceInfo(service="simulation", implementation=f"{IMPLEMENTATION} for Marsden API {self.version}",
                           mode="live", contract_version=INTEGRATION_VERSION)

    async def capabilities(self, request_id: str) -> CapabilitiesResponse:
        catalog = await self._json("GET", "/api/rotations/assessment/catalog", request_id, "catalog")
        api = await self._json("GET", "/openapi.json", request_id, "version")
        self.version = str(api.get("info", {}).get("version", "unknown"))[:20]
        offered = {s.get("id") for s in catalog.get("scenarios", [])}
        self.rotation_ids = [str(r["id"]) for r in catalog.get("rotations", []) if r.get("id")]
        return CapabilitiesResponse(service=self._info(), simulation=SimulationCapabilities(
            compare_rotations=bool(catalog.get("rotations")) and bool(self.fields),
            scenarios=[ours for ours, theirs in SCENARIOS.items() if theirs in offered],
            max_rotations=max(1, len(catalog.get("rotations", []))), result_ttl_s=int(TTL.total_seconds()),
            async_jobs=False))

    async def compare(self, request: ComparisonRequest) -> ComparisonResponse:
        want, now = request.comparison, datetime.now(UTC)
        base: dict[str, Any] = dict(
            contract_version=INTEGRATION_VERSION, request_id=request.request_id, service=self._info(),
            result_id=f"cmp_{uuid.uuid4().hex[:16]}", field_id=want.field_id,
            field_context_revision=want.field_context_revision, scenario=want.scenario,
            engine=EngineInfo(name="marsden-rotation-assessment", version=self.version), created_at=now,
            expires_at=now + TTL)

        def failed(code: str, key: str) -> ComparisonResponse:
            return self._keep(ComparisonResponse(**base, status="failed",
                                                 failure=ComparisonFailure(code=code, message_key=key)))

        field = self.fields.get(want.field_id)
        if field is None:
            return failed("insufficient_data", "error.field_not_covered")
        if want.scenario not in SCENARIOS:
            return failed("unsupported_scenario", "error.unsupported_scenario")
        if not self.rotation_ids:
            await self.capabilities(request.request_id)
        if set(want.rotation_ids) - set(self.rotation_ids):
            return failed("insufficient_data", "error.unknown_rotation")
        # Marsden compares exactly the two plans of its catalog; a request for one keeps only that one.
        data = await self._json("POST", "/api/rotations/assessment", request.request_id, "assessment", {
            "field_id": field, "scenarios": [SCENARIOS[want.scenario]], "rotation_ids": self.rotation_ids})
        try:
            result = next(r for r in data["results"] if r["scenario"]["id"] == SCENARIOS[want.scenario])
            if want.rotation_ids:
                result = {**result, "rotations": [r for r in result["rotations"] if r["rotation"]["id"] in want.rotation_ids]}
            comparison, notes = _comparison(want.scenario, result, self.version, now)
        except (KeyError, TypeError, ValueError, StopIteration) as e:
            raise IntegrationFailure("invalid_response", f"Marsden result does not have the expected shape ({type(e).__name__})") \
                from None
        if comparison is None:
            return failed("insufficient_data", "error.no_water_shortfall")
        warnings = [IntegrationWarning(code="other", detail=n[:300]) for n in notes]
        # Always said, not only when conditions were sent: otherwise the agent offers to "take conditions into account".
        warnings.append(IntegrationWarning(code="other", detail="This service does not use the farmer's conditions "
                                           "(irrigation, fertilizer, preferred crops): it always assumes no irrigation."))
        return self._keep(ComparisonResponse(**base, status="completed", computed_at=now, comparison=comparison,
                                             warnings=warnings))

    def _keep(self, response: ComparisonResponse) -> ComparisonResponse:
        now = datetime.now(UTC)
        self.results = {k: v for k, v in self.results.items() if v.expires_at > now}
        self.results[response.result_id] = response
        return response

    async def result(self, result_id: str, request_id: str) -> ComparisonResponse:
        response = self.results.get(result_id)
        if response is None:
            raise IntegrationFailure("not_found", "unknown result id")
        if response.expires_at <= datetime.now(UTC):
            raise IntegrationFailure("expired", "result has expired")
        return response.model_copy(update={"request_id": request_id})

    async def aclose(self) -> None:
        await self.client.aclose()


def _comparison(scenario: str, result: dict, version: str, now: datetime) -> tuple[RotationComparison | None, list[str]]:
    """Marsden's result for one scenario as the contract's comparison, or None when it computed no water shortfall."""
    sc = result["scenario"]
    years = [int(y) for y in sc["replay_years"]]
    temporal = Temporal(start=date(years[0], 5, 1), end=date(years[-1], 9, 30), aggregation="May-September seasons")
    where = f"weather of {years[0]}-{years[-1]} ({WORDS.get(sc['id'], sc['id'])} {sc['analog_year']} of the field's archive)"
    rotations, notes = [], set()

    def score(block: dict | None, value_key: str, unit: str, what: str) -> Score | None:
        if not block or block.get(value_key) is None:
            return None
        if block.get("accuracy") not in (None, "not_validated_for_field"):
            notes.add(f"Marsden reports accuracy '{block['accuracy']}' for {what}; not passed on as a validation.")
        if block.get("validation_error_pct_points") is not None:
            notes.add(f"Marsden reports a field error for {what} without a sample size; not passed on as a validation.")
        if block.get("method") == "formula":
            prov = Provenance(kind="deterministic_calc", data_status="live", temporal=temporal, retrieved_at=now,
                              method=f"{what}; {where}; not checked on this field")
        else:
            prov = Provenance(kind="ml_estimate", data_status="live", temporal=temporal, retrieved_at=now,
                              model=ModelInfo(name="FAO-33 yield response to water (Ky)", version=f"Marsden {version}"),
                              method=f"{what}; {where}; not checked on this field")
        return Score(value=float(block[value_key]), unit=unit, provenance=prov)

    for rank, rot in enumerate(result["rotations"], start=1):
        water = score(rot.get("water_shortfall"), "value_pct", "%", "FAO-56 water balance: share of crop water need not met")
        if water is None:
            return None, []
        fert = rot.get("fertilizer") or {}
        scores = RotationScores(
            water_risk=water,
            yield_loss_water=score(rot.get("modeled_yield_loss_due_to_water"), "value_pct", "%",
                                   "harvest lost to water shortage, mean of the seasons; heat itself not counted"),
            yield_risk=score(rot.get("yield_risk"), "value_pct", "%", "chance of a harvest loss beyond a threshold"),
            n_input=score(fert if fert.get("n_kg_ha") is not None else None, "n_kg_ha", "kg/ha", "nitrogen rate"),
        )
        crops = rot["rotation"]["crop_ids"]
        rotations.append(RotationOption(
            rotation_id=rot["rotation"]["id"], rank=rank,
            sequence=[RotationStep(year_index=i, season="summer", crop_id=CROPS.get(c, c)) for i, c in enumerate(crops, 1)],
            scores=scores))
        if fert.get("n_kg_ha") is None:
            notes.add("Fertilizer rates are not computed by this service (it needs soil tests and a local method).")
        if (rot.get("yield_risk") or {}).get("value_pct") is None:
            notes.add("The chance of a harvest loss is not computed by this service (it needs local yield records).")
    notes.add(f"Scenario {scenario} is the {WORDS.get(sc['id'], sc['id'])} {sc['analog_year']} of this field's recent "
              "weather archive, as Marsden picked it. Rank is the order Marsden returned, not a recommendation.")
    return RotationComparison(scenario=scenario, rotations=rotations), sorted(notes)
