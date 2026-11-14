"""Wire primitives. JSON is strict; cross-field checks run at the Python boundary."""

from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

VERSION = "1.0.0"
Id = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
Text = Annotated[str, Field(min_length=1, max_length=2000)]
Finite = Annotated[float, Field(allow_inf_nan=False)]
NonNegative = Annotated[Finite, Field(ge=0)]
Fraction = Annotated[Finite, Field(ge=0, le=1)]
Season = Annotated[int, Field(ge=1900, le=2200)]
Revision = Annotated[int, Field(ge=0)]
Checksum = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
CropId = Literal["Wheat", "Maize", "Soy", "Barley", "Sunflower", "Rapeseed", "Peas", "Rice"]
Capability = Literal[
    "phenology",
    "yield",
    "root_zone_water",
    "surface_moisture",
    "irrigation",
    "state_carry",
    "winter",
    "snow",
    "frozen_soil",
    "paddy",
    "salinity",
    "fertility",
    "waterlogging",
    "inundation",
    "solar_geometry",
]
SuitabilityStatus = Literal[
    "eligible",
    "ineligible",
    "insufficient_data",
    "out_of_domain",
    "unsupported",
    "error",
]
OutputStatus = Literal[
    "available",
    "insufficient_data",
    "out_of_domain",
    "unsupported",
    "not_applicable",
    "error",
]
TrainingStatus = Literal["not_trained", "insufficient_training_data", "trained", "failed"]
SolverStatus = Literal["not_run", "optimal", "feasible", "infeasible", "limit_reached", "error"]
RunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


def utc(value: datetime) -> datetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamps must use UTC (Z or +00:00)")
    return value


Utc = Annotated[AwareDatetime, AfterValidator(utc)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Versioned(Contract):
    schema_version: Literal["1.0.0"] = VERSION


class DateWindow(Contract):
    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("window end precedes start")
        return self


# Kept here so all list-identity checks use the same semantics.
def unique(values, label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {label}")


class EvidenceRef(Contract):
    id: Id
    kind: Literal["source", "rule", "model", "constraint", "assumption", "result"]
    version: Id
    locator: Text


class Reason(Contract):
    code: Id
    message: Text
    evidence: Annotated[list[EvidenceRef], Field(min_length=1, max_length=50)]


class SourceRecord(Contract):
    id: Id
    provider: Text
    product: Text
    version: Id
    kind: Literal["measured", "modelled", "user_entered", "synthetic", "reference"]
    acquired_at: Utc
    available_at: Utc
    retrieved_at: Utc
    checksum_sha256: Checksum
    licence: Text
    attribution: Text
    native_resolution_m: NonNegative | None
    coverage_fraction: Fraction | None
    spatial_method: Literal["sensor", "representative_point", "area_weighted", "polygon", "none"]
    quality: Literal["valid", "partial", "missing", "rejected"]

    @model_validator(mode="after")
    def chronological(self):
        if not self.acquired_at <= self.available_at <= self.retrieved_at:
            raise ValueError("source requires acquired_at <= available_at <= retrieved_at")
        return self


class Uncertainty(Contract):
    kind: Literal["none", "scenario_range", "predictive_interval", "measurement_interval"]
    coverage: Fraction | None
    calibration_scope: Text | None
    method_version: Id

    @model_validator(mode="after")
    def calibrated(self):
        if self.kind == "predictive_interval":
            if self.coverage is None or self.coverage <= 0 or not self.calibration_scope:
                raise ValueError(
                    "predictive interval needs positive coverage and calibration scope"
                )
        elif self.kind in ("none", "scenario_range") and self.coverage is not None:
            raise ValueError("a scenario range is not a calibrated coverage probability")
        return self


class Metric(Contract):
    status: OutputStatus
    value: Finite | None
    lower: Finite | None
    upper: Finite | None
    unit: Literal["t/ha", "mm", "m3/m3", "days", "currency/ha", "currency", "ha", "1"]
    uncertainty: Uncertainty
    reasons: Annotated[list[Reason], Field(max_length=50)]

    @model_validator(mode="after")
    def values_match_status(self):
        if self.status != "available":
            if any(v is not None for v in (self.value, self.lower, self.upper)) or not self.reasons:
                raise ValueError("unavailable metrics require null values and reasons")
        else:
            if self.value is None and (self.lower is None or self.upper is None):
                raise ValueError("available metric requires value or complete interval")
            if (self.lower is None) != (self.upper is None):
                raise ValueError("interval bounds must both be present")
            if self.lower is not None and self.upper is not None:
                if self.lower > self.upper:
                    raise ValueError("interval must be ordered")
                if self.value is not None and not self.lower <= self.value <= self.upper:
                    raise ValueError("point estimate must lie within interval")
            if self.uncertainty.kind != "none" and self.lower is None:
                raise ValueError("uncertainty interval needs bounds")
        return self


class CropRef(Contract):
    # Unknown IDs are accepted at the boundary so providers can return unsupported.
    crop_id: Id
    cultivar_id: Id | None
    profile_version: Id | None


class CropRegistryEntry(Contract):
    crop_id: CropId
    family: Id
    required_capabilities: Annotated[list[Capability], Field(max_length=20)]
    note: Text


class CropRegistry(Versioned):
    registry_version: Id
    entries: Annotated[list[CropRegistryEntry], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def distinct(self):
        unique([e.crop_id for e in self.entries], "crop")
        return self
