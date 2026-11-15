from datetime import date
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files
from json import dumps
from math import cos, pi, sin
from typing import Literal

from pydantic import BaseModel, Field, FiniteFloat, model_validator

from field_shift_science.geo_ml.ifs import HORIZON, IFSForecastDay, IFSForecastRun
from field_shift_science.geo_ml.moisture_model import station_distance_km

FORECAST_FEATURES = (
    "surface_moisture",
    "subsurface_moisture",
    "run_initial_day_moisture",
    "soil_temperature_c",
    "air_temperature_c",
    "cumulative_precipitation_mm",
    "cumulative_et0_mm",
    "lead_days",
    "season_sin",
    "season_cos",
)
ARTIFACT_NAME = "artifacts/moisture-ifs-v1.json"


def forecast_features(run: IFSForecastRun, day: IFSForecastDay) -> tuple[float, ...]:
    previous = run.days[: day.lead_days]
    phase = 2 * pi * (day.date.timetuple().tm_yday - 1) / 365.2425
    return (
        day.surface_moisture,
        day.subsurface_moisture,
        run.initial_moisture,
        day.soil_temperature_c,
        day.air_temperature_c,
        sum(item.precipitation_mm for item in previous),
        sum(item.et0_mm for item in previous),
        day.lead_days,
        sin(phase),
        cos(phase),
    )


def forecast_version(payload: dict) -> str:
    encoded = dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return "fieldshift-ifs-" + sha256(encoded).hexdigest()[:12]


class ForecastCorrection(BaseModel, frozen=True):
    version: str = Field(pattern=r"^fieldshift-ifs-[0-9a-f]{12}$")
    schema_version: Literal[1]
    source_cycle: Literal["49R1", "50R1"]
    feature_names: tuple[str, ...]
    intercept: FiniteFloat
    weights: tuple[FiniteFloat, ...]
    interval_half_width: tuple[FiniteFloat, ...]
    fit_stations: tuple[tuple[FiniteFloat, FiniteFloat], ...]
    validated_months: tuple[int, ...]
    promoted: bool
    validation: dict

    @model_validator(mode="after")
    def validate_artifact(self):
        if (
            self.feature_names != FORECAST_FEATURES
            or len(self.weights) != len(FORECAST_FEATURES)
            or len(self.interval_half_width) != HORIZON
            or any(not 0 < width <= 1 for width in self.interval_half_width)
            or (self.promoted and not self._valid_release_gate())
            or self.version != forecast_version(self.model_dump(mode="json", exclude={"version"}))
        ):
            raise ValueError("Invalid soil forecast model artifact")
        return self

    def _valid_release_gate(self) -> bool:
        gate = self.validation.get("gate")
        leads = self.validation.get("leads")
        return (
            isinstance(gate, dict)
            and set(gate)
            == {
                "farm_coverage",
                "improvement",
                "interval_coverage",
                "lead_and_farm_stability",
                "serving_cycle_validated",
            }
            and all(value is True for value in gate.values())
            and isinstance(leads, dict)
            and set(leads) == {str(lead) for lead in range(1, HORIZON + 1)}
            and all(
                isinstance(lead, dict)
                and type(lead.get("interval_coverage")) in (int, float)
                and 0 <= lead["interval_coverage"] <= 1
                for lead in leads.values()
            )
        )

    def supports(self, latitude: float, longitude: float, day: date) -> bool:
        return day.month in self.validated_months and any(
            station_distance_km(latitude, longitude, *station) <= 180
            for station in self.fit_stations
        )

    def predict(self, run: IFSForecastRun, day: IFSForecastDay) -> float:
        correction = self.intercept + sum(
            weight * value
            for weight, value in zip(self.weights, forecast_features(run, day), strict=True)
        )
        return min(1.0, max(0.0, day.surface_moisture + correction))


@lru_cache(maxsize=1)
def get_forecast_correction() -> ForecastCorrection | None:
    try:
        content = files(__package__).joinpath(ARTIFACT_NAME).read_bytes()
    except FileNotFoundError:
        return None
    return ForecastCorrection.model_validate_json(content)
