from datetime import date as CalendarDate
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_serializer,
    model_validator,
)

from field_shift.modules.farm.photos import PHOTO_ERROR, decode_observation_photo

Priority = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Equipment = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
Budget = Annotated[float, Field(ge=0, le=10_000_000_000)]
ObservationKind = Literal["soil_photo", "field_note"]
ObservationCategory = Literal[
    "general", "crop_condition", "weeds", "pests", "disease", "water", "soil"
]
Latitude = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
Longitude = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]


class FarmDefaults(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    irrigation_available: bool
    priority: Priority
    equipment: Equipment | None
    budget: Budget | None


class DefaultsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    irrigation_available: bool | None = None
    priority: Priority | None = None
    equipment: Equipment | None = None
    budget: Budget | None = None

    @model_validator(mode="after")
    def require_values_for_nonnullable_fields(self) -> "DefaultsPatch":
        for name in {"irrigation_available", "priority"} & self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class ObservationCreate(BaseModel):
    field_id: Annotated[str, StringConstraints(min_length=1, max_length=24)]
    kind: ObservationKind
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    date: CalendarDate | None = None
    category: ObservationCategory | None = None
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    photo_jpeg: bytes | None = None
    client_id: UUID | None = None

    @field_validator("photo_jpeg", mode="before")
    @classmethod
    def decode_photo(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(PHOTO_ERROR)
        return decode_observation_photo(value)

    @model_validator(mode="after")
    def require_coordinate_pair(self) -> "ObservationCreate":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Provide both latitude and longitude.")
        return self


class ObservationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    field_id: str
    kind: ObservationKind
    text: str
    date: CalendarDate
    category: ObservationCategory | None = None
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    has_photo: bool | None = None

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        return {key: value for key, value in handler(self).items() if value is not None}


class FarmRead(BaseModel):
    name: str
    season: int
    total_area_ha: float
    defaults: FarmDefaults
    observations: list[ObservationRead]


class ScoutingEvidence(BaseModel):
    id: int
    category: ObservationCategory
    date: CalendarDate
