"""Development/test providers built only from the M0 synthetic fixtures.

Every value they return is labelled data_status "fixture" (by the fixtures themselves) and the
responding implementation reports mode "fixture" (in process) or "stub" (the contract test
server). They exist so the agent, UI and tests can run before members 1 and 2 ship their
services. They are NOT a geo pipeline or a crop model and must never be configured in production.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from contracts.integration import (
    INTEGRATION_VERSION,
    CapabilitiesResponse,
    ComparisonFailure,
    ComparisonRequest,
    ComparisonResponse,
    DatasetCapability,
    EngineInfo,
    FieldProfileRequest,
    FieldProfileResponse,
    GeoCapabilities,
    IntegrationWarning,
    ServiceInfo,
    SimulationCapabilities,
)
from contracts.models import FieldContext, FieldProfile, RotationComparison
from contracts.resources import FIXTURES, field_context_fixtures

from .ports import IntegrationFailure

RESULT_TTL_S = 3600
# The only field with a fixture comparison, and the only scenario it covers.
COMPARISON_FIELDS = {"demo-poltava-01": "rotation_comparison.poltava.json"}

FixtureMode = Literal["fixture", "stub"]


def _contexts() -> dict[str, FieldContext]:
    out = {}
    for path in field_context_fixtures():
        ctx = FieldContext.model_validate_json(path.read_text(encoding="utf-8"))
        out[ctx.field_id] = ctx
    return out


def _info(service: Literal["geo", "simulation"], mode: FixtureMode) -> ServiceInfo:
    name = "fieldshift-contract-stub 0.1" if mode == "stub" else "fieldshift-agent fixture provider"
    return ServiceInfo(service=service, implementation=name, mode=mode, contract_version=INTEGRATION_VERSION)


class FixtureGeoProvider:
    def __init__(self, mode: FixtureMode = "fixture"):
        self.mode = "fixture"
        self.service_mode = mode
        self.contexts = _contexts()

    async def capabilities(self, request_id: str) -> CapabilitiesResponse:
        layers = {la.layer_id: la for ctx in self.contexts.values() for la in ctx.available_layers}
        datasets = [DatasetCapability(dataset_id=lid, kind=la.provenance.kind, available=True,
                                      note="synthetic fixture") for lid, la in sorted(layers.items())]
        return CapabilitiesResponse(service=_info("geo", self.service_mode),
                                    geo=GeoCapabilities(field_profile=True, timeseries=False, datasets=datasets))

    async def field_profile(self, request: FieldProfileRequest) -> FieldProfileResponse:
        ctx = self.contexts.get(request.field_id)
        if ctx is None:
            raise IntegrationFailure("not_found", "no fixture for this field")
        partial = any(f.provenance.data_status == "unavailable" for f in ctx.facts)
        return FieldProfileResponse(
            contract_version=INTEGRATION_VERSION, request_id=request.request_id, service=_info("geo", self.service_mode),
            status="partial" if partial else "complete", generated_at=datetime.now(UTC),
            geometry=ctx.geometry, centroid=ctx.centroid,
            profile=FieldProfile(field_id=ctx.field_id, facts=ctx.facts, timeseries_index=ctx.timeseries_index,
                                 available_layers=ctx.available_layers),
            warnings=[IntegrationWarning(code="fixture_data", detail="synthetic fixture values, not measurements")],
        )

    async def aclose(self) -> None:
        return None


class FixtureSimulationProvider:
    """Returns the M0 synthetic comparison for the Poltava demo field, scenario "typical" only.

    Anything else gets an honest `failed` answer: no numbers are made up for other fields or scenarios.
    """

    def __init__(self, mode: FixtureMode = "fixture", ttl_s: int = RESULT_TTL_S):
        self.mode = "fixture"
        self.service_mode = mode
        self.ttl_s = ttl_s
        self.results: dict[str, ComparisonResponse] = {}
        self.by_key: dict[str, str] = {}

    async def capabilities(self, request_id: str) -> CapabilitiesResponse:
        return CapabilitiesResponse(service=_info("simulation", self.service_mode),
                                    simulation=SimulationCapabilities(compare_rotations=True, scenarios=["typical"],
                                                                      max_rotations=6, result_ttl_s=self.ttl_s))

    async def compare(self, request: ComparisonRequest) -> ComparisonResponse:
        stored = self.results.get(self.by_key.get(request.idempotency_key, ""))
        if stored is not None and stored.expires_at > datetime.now(UTC):
            # same request replayed while the result is valid: same result, nothing recomputed
            return stored.model_copy(update={"request_id": request.request_id})
        want = request.comparison
        now = datetime.now(UTC)
        base = dict(contract_version=INTEGRATION_VERSION, request_id=request.request_id,
                    service=_info("simulation", self.service_mode),
                    result_id=f"cmp_{uuid.uuid4().hex[:16]}", field_id=want.field_id,
                    field_context_revision=want.field_context_revision, scenario=want.scenario,
                    engine=EngineInfo(name="fixture-replay", version=f"m0-{INTEGRATION_VERSION}"),
                    created_at=now, expires_at=now + timedelta(seconds=self.ttl_s),
                    warnings=[IntegrationWarning(code="fixture_data", detail="synthetic fixture result; no crop model ran")])
        fixture = COMPARISON_FIELDS.get(want.field_id)
        if fixture is None or want.scenario != "typical":
            failure = ComparisonFailure(code="unsupported_scenario" if fixture else "insufficient_data",
                                        message_key="error.simulation_no_fixture")
            response = ComparisonResponse(**base, status="failed", failure=failure)
        else:
            comparison = RotationComparison.model_validate_json((FIXTURES / fixture).read_text(encoding="utf-8"))
            if want.rotation_ids:
                kept = [r for r in comparison.rotations if r.rotation_id in want.rotation_ids]
                if not kept:
                    response = ComparisonResponse(**base, status="failed", failure=ComparisonFailure(
                        code="insufficient_data", message_key="error.simulation_unknown_rotation"))
                    self._store(request, response)
                    return response
                comparison = comparison.model_copy(update={"rotations": kept})
            response = ComparisonResponse(**base, status="completed", computed_at=now, comparison=comparison)
        self._store(request, response)
        return response

    def _store(self, request: ComparisonRequest, response: ComparisonResponse) -> None:
        self.results[response.result_id] = response
        self.by_key[request.idempotency_key] = response.result_id

    async def result(self, result_id: str, request_id: str) -> ComparisonResponse:
        found = self.results.get(result_id)
        if found is None:
            raise IntegrationFailure("not_found", "unknown result id")
        if found.expires_at <= datetime.now(UTC):
            raise IntegrationFailure("expired", "result has expired")
        return found.model_copy(update={"request_id": request_id})

    async def aclose(self) -> None:
        return None

