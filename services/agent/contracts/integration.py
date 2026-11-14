"""Service integration contracts, v0.1 — PROPOSED, not yet agreed with members 1 and 2.

Envelopes for the agent's calls to the geo service (member 1) and the simulation service
(member 2). They wrap the M0 payloads (`FieldProfile`, `CompareRotationsRequest`,
`RotationComparison`) instead of redefining them, and add only what crossing a service
boundary needs: identity of the responding implementation, contract version, request ids,
status, timestamps, result ids and expiry. The M0 schema is unchanged.

Exported to contracts/schema/fieldshift-integration.schema.json (`python -m contracts.export`).
Spec and examples: docs/M5_TEAM_INTEGRATION_CONTRACT.md.
"""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .models import (
    CompareRotationsRequest,
    FieldProfile,
    Id,
    Model,
    Point,
    Polygon,
    RotationComparison,
    ScenarioId,
    ShortText,
    UtcDatetime,
)

INTEGRATION_VERSION = "0.1"
ContractVersion = Literal["0.1"]

# How the responding implementation produced its data. Only "live" may ever carry real
# observations or computations; "stub" (contract test server) and "fixture" (in-process
# test data) must label every value data_status "fixture".
ServiceMode = Literal["live", "stub", "fixture"]

IntegrationErrorCode = Literal[
    "invalid_request", "not_found", "field_not_authorized", "unsupported_scenario",
    "unsupported_contract", "expired", "unavailable", "internal",
]


class ServiceInfo(Model):
    service: Literal["geo", "simulation"]
    implementation: ShortText          # e.g. "fieldshift-geo 0.3.1", "fieldshift-contract-stub 0.1"
    mode: ServiceMode
    contract_version: ContractVersion


class IntegrationWarning(Model):
    code: Literal["partial_data", "dataset_unavailable", "unverified_version", "coarse_resolution",
                  "model_not_validated", "fixture_data", "other"]
    detail: ShortText | None = None


class IntegrationErrorBody(Model):
    code: IntegrationErrorCode
    message_key: ShortText             # i18n key, e.g. "error.field_not_found"
    retryable: bool
    request_id: Id | None = None


class IntegrationError(Model):
    """Body of every non-2xx response from the geo or simulation service."""

    error: IntegrationErrorBody


# ---------------------------------------------------------------- capabilities

class DatasetCapability(Model):
    dataset_id: Id                     # e.g. "smap_l4_rootzone", "nasa_power_daily", "soilgrids_ph"
    kind: Literal["satellite_product", "ml_estimate", "deterministic_calc", "reference"]
    available: bool
    note: ShortText | None = None


class GeoCapabilities(Model):
    field_profile: bool
    timeseries: bool
    datasets: list[DatasetCapability] = []


class SimulationCapabilities(Model):
    compare_rotations: bool
    scenarios: list[ScenarioId] = []
    max_rotations: Annotated[int, Field(ge=1, le=20)] = 6
    result_ttl_s: Annotated[int, Field(ge=60)] = 3600
    async_jobs: bool = False           # true: POST may answer "accepted"/"running"; poll GET /comparisons/{id}


class CapabilitiesResponse(Model):
    """GET /v1/capabilities on either service. A capability is an offer, never a completed computation."""

    service: ServiceInfo
    geo: GeoCapabilities | None = None
    simulation: SimulationCapabilities | None = None

    @model_validator(mode="after")
    def _matches_service(self):
        if self.service.service == "geo" and self.geo is None:
            raise ValueError("geo service must report geo capabilities")
        if self.service.service == "simulation" and self.simulation is None:
            raise ValueError("simulation service must report simulation capabilities")
        return self


# ---------------------------------------------------------------- geo: field profile

class FieldProfileRequest(Model):
    """POST /v1/field-profile (geo service)."""

    contract_version: ContractVersion
    request_id: Id
    field_id: Id


class FieldProfileResponse(Model):
    contract_version: ContractVersion
    request_id: Id
    service: ServiceInfo
    status: Literal["complete", "partial"]
    generated_at: UtcDatetime
    # Where the geo service has this field: checked against the host's FieldContext geometry.
    geometry: Point | Polygon
    centroid: tuple[float, float]      # [lon, lat]
    profile: FieldProfile
    warnings: list[IntegrationWarning] = []


# ---------------------------------------------------------------- simulation: rotation comparison

class EngineInfo(Model):
    name: ShortText                    # e.g. "fieldshift-rotation-sim"
    version: ShortText                 # algorithm / model version


class ComparisonRequest(Model):
    """POST /v1/comparisons (simulation service). Send `Idempotency-Key: <idempotency_key>` too."""

    contract_version: ContractVersion
    request_id: Id
    idempotency_key: Id
    comparison: CompareRotationsRequest


class ComparisonFailure(Model):
    code: Literal["insufficient_data", "unsupported_scenario", "engine_error", "timeout"]
    message_key: ShortText


class ComparisonResponse(Model):
    """Answer to POST /v1/comparisons and GET /v1/comparisons/{result_id}."""

    contract_version: ContractVersion
    request_id: Id
    service: ServiceInfo
    result_id: Id
    status: Literal["accepted", "running", "completed", "failed"]
    field_id: Id
    field_context_revision: Annotated[int, Field(ge=0)]
    scenario: ScenarioId
    engine: EngineInfo
    created_at: UtcDatetime
    computed_at: UtcDatetime | None = None   # set when completed
    expires_at: UtcDatetime                  # after this the result may be gone: hosts must reject it
    comparison: RotationComparison | None = None
    failure: ComparisonFailure | None = None
    warnings: list[IntegrationWarning] = []

    @model_validator(mode="after")
    def _status_fields(self):
        if self.status == "completed" and (self.comparison is None or self.computed_at is None):
            raise ValueError("a completed comparison needs comparison and computed_at")
        if self.status != "completed" and self.comparison is not None:
            raise ValueError("only a completed comparison may carry results")
        if self.status == "failed" and self.failure is None:
            raise ValueError("a failed comparison needs failure")
        if self.comparison is not None and self.comparison.scenario != self.scenario:
            raise ValueError("comparison.scenario must equal scenario")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        return self
