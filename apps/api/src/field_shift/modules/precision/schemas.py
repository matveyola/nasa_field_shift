from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from field_shift.modules.fields.schemas import BoundaryRevision, Season

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Rate = Annotated[float, Field(ge=0, le=10_000_000, allow_inf_nan=False)]
Operation = Literal["fertilization", "sowing", "spraying"]
Zone = Literal["low", "typical", "high"]
UNITS = {"fertilization": "kg/ha", "sowing": "seeds/ha", "spraying": "L/ha"}


class PrecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    client_id: UUID
    season: Season
    boundary_revision: BoundaryRevision
    geometry_fingerprint: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    name: Name
    product: Name
    operation: Operation
    fallback_rate: Rate


class PrescriptionCreate(PrecisionCreate):
    observation_date: date
    scene_id: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    rates: dict[Zone, Rate]


class Treatment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    rate: Rate


class TrialCreate(PrecisionCreate):
    treatments: Annotated[list[Treatment], Field(min_length=2, max_length=4)]
    replicates: Annotated[int, Field(ge=3, le=12)] = 3
    strip_width_m: Annotated[float, Field(ge=3, le=100)] = 12
    heading_degrees: Annotated[float, Field(ge=0, lt=180)] = 0
    headland_m: Annotated[float, Field(ge=0, le=100)] = 12
    seed: Annotated[int, Field(ge=0, le=2**31 - 1)] = 42

    @model_validator(mode="after")
    def unique_names(self):
        names = [treatment.name.casefold() for treatment in self.treatments]
        if len(set(names)) != len(names):
            raise ValueError("Treatment names must be distinct.")
        return self


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Annotated[int, Field(ge=1)]
    confirmed_rates_and_geometry: Literal[True]
    confirmed_zero_rates: bool = False


class HarvestImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Annotated[int, Field(ge=1)]
    file_name: Name
    csv: Annotated[str, Field(min_length=1, max_length=1_000_000)]
