"""Field inputs and agronomic suitability, independent of database and providers."""

from datetime import date
from typing import Annotated, Literal

from pydantic import Field, model_serializer, model_validator

from .common import (
    Capability,
    Contract,
    CropRef,
    DateWindow,
    Finite,
    Fraction,
    Id,
    NonNegative,
    Reason,
    Revision,
    Season,
    SourceRecord,
    SuitabilityStatus,
    Utc,
    Versioned,
    unique,
)

Longitude = Annotated[Finite, Field(ge=-180, le=180)]
Latitude = Annotated[Finite, Field(ge=-90, le=90)]
Position = tuple[Longitude, Latitude]


class FieldGeometry(Contract):
    type: Literal["Polygon"]
    crs: Literal["EPSG:4326"]
    coordinates: Annotated[
        list[Annotated[list[Position], Field(min_length=4, max_length=5000)]],
        Field(min_length=1, max_length=50),
    ]

    @model_validator(mode="after")
    def closed_rings(self):
        for ring in self.coordinates:
            if ring[0] != ring[-1] or len(set(ring[:-1])) < 3:
                raise ValueError("rings must close and contain three distinct vertices")
            if any(abs(a[0] - b[0]) > 180 for a, b in zip(ring, ring[1:])):
                raise ValueError("split antimeridian geometry before planning-v1")
            area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:]))
            if abs(area) < 1e-12:
                raise ValueError("degenerate polygon ring")
        return self


class SoilHorizon(Contract):
    top_cm: Annotated[Finite, Field(ge=0, le=1000)]
    bottom_cm: Annotated[Finite, Field(gt=0, le=1000)]
    sand_fraction: Fraction | None
    silt_fraction: Fraction | None
    clay_fraction: Fraction | None
    field_capacity_m3_m3: Fraction | None
    wilting_point_m3_m3: Fraction | None
    saturation_m3_m3: Fraction | None
    organic_carbon_g_kg: Annotated[Finite, Field(ge=0, le=1000)] | None
    ph: Annotated[Finite, Field(ge=0, le=14)] | None
    salinity_ds_m: NonNegative | None
    source_ids: Annotated[list[Id], Field(min_length=1, max_length=20)]
    quality: Literal["valid", "partial", "missing", "rejected"]

    @model_validator(mode="after")
    def physical_bounds(self):
        if self.bottom_cm <= self.top_cm:
            raise ValueError("soil bottom must be deeper than top")
        fractions = [self.sand_fraction, self.silt_fraction, self.clay_fraction]
        if all(v is not None for v in fractions) and abs(sum(fractions) - 1) > 0.02:
            raise ValueError("soil texture fractions must sum to 1 within 0.02")
        capacities = [
            self.wilting_point_m3_m3,
            self.field_capacity_m3_m3,
            self.saturation_m3_m3,
        ]
        known = [v for v in capacities if v is not None]
        if known != sorted(known):
            raise ValueError("require wilting point <= field capacity <= saturation")
        return self


class WaterState(Contract):
    observed_at: Utc
    available_at: Utc
    depth_top_cm: NonNegative
    depth_bottom_cm: NonNegative
    volumetric_m3_m3: Fraction | None
    available_reserve_mm: NonNegative | None
    available_capacity_mm: NonNegative | None
    source_ids: Annotated[list[Id], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def coherent(self):
        if self.available_at < self.observed_at or self.depth_bottom_cm <= self.depth_top_cm:
            raise ValueError("invalid water observation chronology or depth")
        if self.available_capacity_mm is not None and self.available_reserve_mm is not None:
            if self.available_reserve_mm > self.available_capacity_mm:
                raise ValueError("available reserve exceeds available capacity")
        return self


class CropHistoryEntry(Contract):
    season: Season
    crop: CropRef | None
    status: Literal["confirmed", "reported", "unknown"]
    sowing_date: date | None
    harvest_date: date | None
    source_ids: Annotated[list[Id], Field(max_length=20)]

    @model_validator(mode="after")
    def dates(self):
        if self.status == "unknown" and self.crop is not None:
            raise ValueError("unknown history cannot assert a crop")
        if self.status != "unknown" and (self.crop is None or not self.source_ids):
            raise ValueError("known history requires a crop and provenance")
        if self.sowing_date and self.harvest_date and self.harvest_date < self.sowing_date:
            raise ValueError("harvest precedes sowing")
        return self


class IrrigationLimit(Contract):
    season: Season
    max_mm: NonNegative | None
    system: Literal["none", "drip", "sprinkler", "surface", "managed_paddy", "unknown"]
    source_ids: Annotated[list[Id], Field(max_length=20)]

    @model_validator(mode="after")
    def no_water(self):
        if self.system == "none" and self.max_mm != 0:
            raise ValueError("no irrigation system must explicitly have zero water")
        return self


class Management(Contract):
    production_system: Literal["outdoor"]
    irrigation: Annotated[list[IrrigationLimit], Field(max_length=10)]
    equipment_ids: Annotated[list[Id], Field(max_length=100)]
    residue_cover_fraction: Fraction | None
    tillage: Literal["conventional", "reduced", "no_till", "unknown"]

    @model_validator(mode="after")
    def distinct(self):
        unique([i.season for i in self.irrigation], "irrigation season")
        unique(self.equipment_ids, "equipment")
        return self


class TerrainRadiation(Contract):
    latitude_deg: Latitude
    longitude_deg: Longitude
    elevation_m: Annotated[Finite, Field(ge=-500, le=9000)] | None
    slope_deg: Annotated[Finite, Field(ge=0, le=90)] | None
    aspect_deg: Annotated[Finite, Field(ge=0, lt=360)] | None
    aspect_convention: Literal["clockwise_from_true_north"]
    solar_time: Utc | None
    solar_zenith_deg: Annotated[Finite, Field(ge=0, le=180)] | None
    surface_shortwave_w_m2: NonNegative | None
    source_ids: Annotated[list[Id], Field(max_length=20)]

    @model_validator(mode="after")
    def solar_context(self):
        if self.slope_deg == 0 and self.aspect_deg is not None:
            raise ValueError("flat terrain has no aspect")
        if self.solar_zenith_deg is not None or self.surface_shortwave_w_m2 is not None:
            if self.solar_time is None:
                raise ValueError("solar measurements need a UTC time")
        return self


class HazardEvidence(Contract):
    id: Id
    kind: Literal["wind", "frost", "heat", "heavy_rain", "river_discharge", "inundation"]
    status: Literal["available", "insufficient_data", "unsupported", "error"]
    indicator: Literal["threshold", "efi", "river_warning", "hydraulic_depth"]
    value: Finite | None
    unit: Literal["m/s", "degC", "mm/day", "1", "m3/s", "m"]
    valid_window: DateWindow
    source_ids: Annotated[list[Id], Field(max_length=20)]
    reasons: Annotated[list[Reason], Field(max_length=50)]

    @model_validator(mode="after")
    def evidence(self):
        if self.status == "available" and (self.value is None or not self.source_ids):
            raise ValueError("available hazard needs value and sources")
        if self.status != "available" and (self.value is not None or not self.reasons):
            raise ValueError("unavailable hazard needs null and reasons")
        units = {"efi": "1", "river_warning": "m3/s", "hydraulic_depth": "m"}
        if self.indicator in units and self.unit != units[self.indicator]:
            raise ValueError("hazard indicator/unit mismatch")
        return self


class FieldProfileSnapshot(Versioned):
    snapshot_id: Id
    farm_id: Id
    field_id: Id
    profile_revision: Revision
    geometry_revision: Revision
    as_of: Utc
    geometry: FieldGeometry
    area_ha: Annotated[Finite, Field(gt=0, le=100000)]
    land_status: Literal["arable", "non_arable", "ocean", "ice", "unknown"]
    land_source_ids: Annotated[list[Id], Field(max_length=200)] = Field(default_factory=list)
    history: Annotated[list[CropHistoryEntry], Field(max_length=100)]
    history_complete_since: Season | None
    current_crop: CropRef | None
    soil: Annotated[list[SoilHorizon], Field(max_length=30)]
    water_state: WaterState | None
    management: Management
    terrain: TerrainRadiation | None
    hazards: Annotated[list[HazardEvidence], Field(max_length=50)]
    sources: Annotated[list[SourceRecord], Field(max_length=200)]
    missing_data: Annotated[list[Reason], Field(max_length=50)]

    @model_serializer(mode="wrap")
    def preserve_legacy_land_serialization(self, handler):
        data = handler(self)
        # Empty and absent bindings both mean unassessed. Keep pre-extension
        # canonical snapshot/request bytes stable; explicit links are hashed.
        if not self.land_source_ids:
            data.pop("land_source_ids", None)
        return data

    @model_validator(mode="after")
    def provenance(self):
        unique([s.id for s in self.sources], "source")
        unique([h.season for h in self.history], "history season")
        if any(s.available_at > self.as_of for s in self.sources):
            raise ValueError("field source was unavailable at as_of")
        if self.water_state and self.water_state.available_at > self.as_of:
            raise ValueError("water observation was unavailable at as_of")
        ordered = sorted(self.soil, key=lambda h: h.top_cm)
        if any(a.bottom_cm > b.top_cm for a, b in zip(ordered, ordered[1:])):
            raise ValueError("soil horizons overlap")
        source_ids = {s.id for s in self.sources}
        unique(self.land_source_ids, "land source")
        if not set(self.land_source_ids) <= source_ids:
            raise ValueError("unresolved land source reference")
        records = [
            *self.history,
            *self.soil,
            *self.management.irrigation,
            *self.hazards,
        ]
        records += [r for r in (self.water_state, self.terrain) if r is not None]
        if any(not set(r.source_ids) <= source_ids for r in records):
            raise ValueError("unresolved field source reference")
        if self.land_status != "unknown" and not self.sources:
            raise ValueError("land assessment needs provenance")
        return self


class SuitabilityAssessment(Versioned):
    assessment_id: Id
    field_snapshot_id: Id
    season: Season
    crop: CropRef
    status: SuitabilityStatus
    rulebook_version: Id
    required_capabilities: Annotated[list[Capability], Field(max_length=20)]
    checked_rule_ids: Annotated[list[Id], Field(max_length=200)]
    unchecked_rule_ids: Annotated[list[Id], Field(max_length=200)]
    reasons: Annotated[list[Reason], Field(min_length=1, max_length=100)]


class CropAssignment(Contract):
    field_id: Id
    season: Season
    crop: CropRef
    sowing_window: DateWindow | None
    harvest_window: DateWindow | None
    locked: bool
    commitment_id: Id | None

    @model_validator(mode="after")
    def commitment(self):
        if self.locked and not self.commitment_id:
            raise ValueError("locked assignment requires a commitment reference")
        return self


class CandidateSequence(Versioned):
    sequence_id: Id
    field_snapshot_id: Id
    field_id: Id
    rulebook_version: Id
    assignments: Annotated[list[CropAssignment], Field(min_length=1, max_length=3)]
    assessments: Annotated[list[SuitabilityAssessment], Field(min_length=1, max_length=3)]

    @model_validator(mode="after")
    def same_scope(self):
        seasons = [a.season for a in self.assignments]
        if seasons != list(range(seasons[0], seasons[0] + len(seasons))):
            raise ValueError("sequence seasons must be ordered and consecutive")
        if any(a.field_id != self.field_id for a in self.assignments):
            raise ValueError("candidate spans multiple fields")
        if len(self.assignments) != len(self.assessments):
            raise ValueError("each assignment requires a suitability assessment")
        for assignment, assessment in zip(self.assignments, self.assessments):
            if (
                assessment.field_snapshot_id != self.field_snapshot_id
                or assessment.rulebook_version != self.rulebook_version
                or assessment.season != assignment.season
                or assessment.crop != assignment.crop
            ):
                raise ValueError("assessment does not match candidate assignment")
        return self
