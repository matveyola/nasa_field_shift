"""Additive experimental handoff transport; planning-v1 contracts stay unchanged."""

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Operation = Literal[
    "forecast",
    "climate_step",
    "climate_compare",
    "assessment",
    "compare_water_balances",
    "sutherland_2027",
]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HandoffSubmission(Input):
    request_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    geometry_revision: int = Field(ge=0)
    operation: Operation
    parameters: dict[str, Any] = Field(default_factory=dict)
    allow_public_data: bool = False

    @model_validator(mode="after")
    def bounded_parameters(self):
        if {
            "field",
            "field_id",
            "as_of",
            "evidence",
            "engine",
            "ruslan_dir",
        } & self.parameters.keys():
            raise ValueError("Field, decision cutoff and engine identities are server-owned")
        if len(json.dumps(self.parameters, allow_nan=False).encode()) > 65_536:
            raise ValueError("Handoff parameters exceed 64 KiB")
        return self


class SourceReceipt(Input):
    source_url: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(ge=0)
    available_at: datetime
    kind: Literal["acquired_public_input", "packaged_reference"]


class HandoffResult(Input):
    operation: Operation
    status: Literal["available", "unavailable", "unsupported"]
    data_kind: Literal[
        "historical_replay", "hybrid_scenario", "conditional_climate", "supplied_balance"
    ]
    empirical_status: Literal["not_validated"] = "not_validated"
    engine_version: str
    source_commit: str
    input_sha256: str
    evidence_sha256: str
    acquired_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    source_receipts: list[SourceReceipt] = Field(default_factory=list)
    limitations: list[str]
    reasons: list[str] = Field(default_factory=list)


class HandoffJob(Input):
    job_id: str
    field_id: str
    geometry_revision: int
    status: Literal["queued", "running", "retrying", "succeeded", "failed"]
    attempts: int
    result: HandoffResult | None = None
    error: str | None = None


class CropCapability(Input):
    id: str
    name: str
    climate_supported: bool
    allocation_crop_id: str | None
    profile: dict[str, Any]


class HandoffCatalog(Input):
    status: Literal["available", "unavailable"]
    engine_version: str
    source_commit: str
    empirical_status: Literal["not_validated"] = "not_validated"
    operations: list[Operation]
    crops: list[CropCapability]
    request_schemas: dict[str, Any]
    limitations: list[str]
    reasons: list[str] = Field(default_factory=list)
