from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from field_shift.modules.fields.geometry import area_hectares, validate_ring

Season = Annotated[int, Field(ge=2000, le=2100)]
CropName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
BoundaryRevision = Annotated[int, Field(ge=0, strict=True)]
MAX_AREA_HA = 100_000


class Polygon(BaseModel):
    type: Literal["Polygon"]
    coordinates: list[list[tuple[float, float]]]

    @model_validator(mode="after")
    def validate_polygon(self) -> "Polygon":
        if len(self.coordinates) != 1:
            raise ValueError("Exactly one exterior ring is supported")
        validate_ring(self.coordinates[0])
        return self


class FieldCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    crop: CropName
    area_ha: Annotated[float, Field(gt=0, le=MAX_AREA_HA)]
    geometry: Polygon
    season: Season | None = None


def _check_area(geometry: Polygon) -> None:
    if not 0 < area_hectares(geometry.coordinates[0]) <= MAX_AREA_HA:
        raise ValueError("The area must be greater than 0 and at most 100,000 hectares.")


class FieldBoundaryUpdate(BaseModel):
    geometry: Polygon
    boundary_revision: BoundaryRevision

    @model_validator(mode="after")
    def validate_area(self) -> "FieldBoundaryUpdate":
        _check_area(self.geometry)
        return self


class FieldPlantingUpdate(BaseModel):
    geometry: Polygon | None
    boundary_revision: BoundaryRevision

    @model_validator(mode="after")
    def validate_area(self) -> "FieldPlantingUpdate":
        if self.geometry is not None:
            _check_area(self.geometry)
        return self


class FieldPlantingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    season: Season
    geometry: Polygon
    area_ha: float


class FieldRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    crop: str
    area_ha: float
    geometry: Polygon
    boundary_revision: BoundaryRevision
    planting: FieldPlantingRead | None = None
