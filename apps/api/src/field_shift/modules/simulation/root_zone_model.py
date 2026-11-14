import logging
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files
from json import dumps
from math import cos, pi, sin
from typing import Literal

from pydantic import BaseModel, Field, FiniteFloat, ValidationError, model_validator

from field_shift.modules.geo_ml.ifs import CURRENT_MODEL_CYCLE, HORIZON
from field_shift.modules.geo_ml.moisture_model import station_distance_km
from field_shift.modules.simulation.root_zone_inputs import (
    RootZoneRun,
    RootZoneWeatherDay,
    root_zone_storage_mm,
)
from field_shift.modules.simulation.water_balance import (
    REFERENCE_FIELD_CAPACITY,
    REFERENCE_WILTING_POINT,
)

logger = logging.getLogger(__name__)
ARTIFACT_NAME = "artifacts/root-zone-ifs-v1.json"
DEPTH_M = 1.0
CAPACITY_MM = round(1000 * DEPTH_M * (REFERENCE_FIELD_CAPACITY - REFERENCE_WILTING_POINT), 4)
FEATURE_NAMES = (
    "physical_reserve_mm",
    "raw_ifs_available_mm",
    "initial_storage_mm",
    "ifs_deep_moisture",
    "soil_temperature_c",
    "air_temperature_c",
    "rain_to_lead_mm",
    "et0_to_lead_mm",
    "lead",
    "season_sin",
    "season_cos",
)
RELEASE_GATES = {
    "adequate_farms",
    "improves_physical_mae_and_rmse",
    "improves_raw_ifs_rmse",
    "interval_coverage",
    "farm_and_lead_stability",
    "serving_cycle_validated",
    "crop_depth_and_irrigation_verified",
}


def reference_reserve_mm(storage_mm: float) -> float:
    return max(0.0, min(CAPACITY_MM, storage_mm - 1000 * DEPTH_M * REFERENCE_WILTING_POINT))


def root_zone_features(
    run: RootZoneRun, day: RootZoneWeatherDay, physical_reserve_mm: float
) -> tuple[float, ...]:
    window = run.days[: day.lead_days]
    phase = 2 * pi * (day.date.timetuple().tm_yday - 1) / 365.2425
    return (
        physical_reserve_mm,
        reference_reserve_mm(root_zone_storage_mm(day.moisture_layers, DEPTH_M)),
        root_zone_storage_mm(run.initial_layers, DEPTH_M),
        day.moisture_layers[2],
        day.soil_temperature_c,
        day.temperature_c,
        sum(item.rain_mm for item in window),
        sum(item.reference_et0_mm for item in window),
        day.lead_days,
        sin(phase),
        cos(phase),
    )


def root_zone_version(payload: dict) -> str:
    encoded = dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return "fieldshift-root-" + sha256(encoded).hexdigest()[:12]


class RootZoneCorrection(BaseModel, frozen=True):
    version: str = Field(pattern=r"^fieldshift-root-[0-9a-f]{12}$")
    schema_version: Literal[1]
    source_cycle: Literal["50R1"]
    reference_depth_m: Literal[1]
    feature_names: tuple[str, ...]
    intercept: FiniteFloat
    weights: tuple[FiniteFloat, ...]
    interval_half_width_mm: tuple[FiniteFloat, ...]
    fit_locations: tuple[tuple[FiniteFloat, FiniteFloat], ...]
    validated_months: tuple[int, ...]
    promoted: bool
    validation: dict

    @model_validator(mode="after")
    def validate_artifact(self):
        if (
            self.feature_names != FEATURE_NAMES
            or len(self.weights) != len(FEATURE_NAMES)
            or len(self.interval_half_width_mm) != HORIZON
            or any(not 0 < width <= CAPACITY_MM for width in self.interval_half_width_mm)
            or not self.fit_locations
            or self.version != root_zone_version(self.model_dump(mode="json", exclude={"version"}))
        ):
            raise ValueError("Invalid root-zone model artifact")
        if self.promoted:
            gates = self.validation.get("gates")
            if (
                self.source_cycle != CURRENT_MODEL_CYCLE
                or not isinstance(gates, dict)
                or set(gates) != RELEASE_GATES
                or any(value is not True for value in gates.values())
            ):
                raise ValueError("Root-zone model lacks production validation")
        return self

    def supports(self, latitude: float, longitude: float, day: RootZoneWeatherDay) -> bool:
        return (
            day.date.month in self.validated_months
            and day.soil_temperature_c >= 2
            and any(
                station_distance_km(latitude, longitude, *location) <= 150
                for location in self.fit_locations
            )
        )

    def predict(
        self, run: RootZoneRun, day: RootZoneWeatherDay, physical_reserve_mm: float
    ) -> float:
        if run.model_cycle != self.source_cycle or day.soil_temperature_c < 2:
            raise ValueError(
                "Root-zone correction requires a matching forecast cycle and thawed soil."
            )
        residual = self.intercept + sum(
            weight * value
            for weight, value in zip(
                self.weights, root_zone_features(run, day, physical_reserve_mm), strict=True
            )
        )
        return max(0.0, min(CAPACITY_MM, physical_reserve_mm + residual))


@lru_cache(maxsize=1)
def get_root_zone_correction() -> RootZoneCorrection | None:
    try:
        raw = files(__package__).joinpath(ARTIFACT_NAME).read_bytes()
    except FileNotFoundError:
        return None
    return RootZoneCorrection.model_validate_json(raw)


def active_root_zone_correction() -> RootZoneCorrection | None:
    try:
        candidate = get_root_zone_correction()
    except (OSError, ValidationError):
        logger.exception("Root-zone correction could not be loaded. Serving the physical bucket.")
        return None
    if (
        candidate is not None
        and candidate.promoted
        and candidate.source_cycle == CURRENT_MODEL_CYCLE
    ):
        return candidate
    return None
