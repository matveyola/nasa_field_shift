from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files
from json import dumps
from math import asin, cos, isfinite, radians, sin, sqrt
from typing import Literal

from pydantic import BaseModel, Field, FiniteFloat, ValidationError, model_validator

ARTIFACT = "artifacts/rotation-yield-v1.json"
SOURCE_URL = "https://doi.org/10.15482/USDA.ADC/1411953"
FEATURES = (
    "wheat_predecessor",
    "latitude",
    "longitude",
    "wheat_latitude",
    "wheat_longitude",
)
RELEASE_CHECKS = frozenset(
    (
        "site_coverage",
        "mae_improvement",
        "rmse_improvement",
        "transition_stability",
        "coverage",
    )
)
Predecessor = Literal["Soy", "Wheat"]


@dataclass(frozen=True, slots=True)
class RotationYieldEstimate:
    delta_t_ha: float
    lower_t_ha: float
    upper_t_ha: float


class RotationYieldModelUnavailable(RuntimeError):
    """The installed rotation model could not be validated."""


def site_distance(latitude: float, longitude: float, other_lat: float, other_lon: float) -> float:
    latitude, longitude, other_lat, other_lon = map(
        radians, (latitude, longitude, other_lat, other_lon)
    )
    angle = sin((other_lat - latitude) / 2) ** 2 + (
        cos(latitude) * cos(other_lat) * sin((other_lon - longitude) / 2) ** 2
    )
    return 12_742 * asin(sqrt(min(1, angle)))


def effect_features(latitude: float, longitude: float, preceding_crop: Predecessor):
    wheat = float(preceding_crop == "Wheat")
    lat = (latitude - 42) / 2
    lon = (longitude + 89) / 4
    return wheat, lat, lon, wheat * lat, wheat * lon


def artifact_version(payload: dict) -> str:
    data = dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "fieldshift-rotation-" + sha256(data.encode()).hexdigest()[:12]


def normalize_crop(crop: str | None) -> str | None:
    if crop is None:
        return None
    return {
        "corn": "Maize",
        "maize": "Maize",
        "soybean": "Soy",
        "soybeans": "Soy",
        "soy": "Soy",
        "wheat": "Wheat",
    }.get(crop.strip().casefold())


class RotationYieldModel(BaseModel, frozen=True):
    schema_version: Literal[1]
    version: str = Field(pattern=r"^fieldshift-rotation-[0-9a-f]{12}$")
    feature_names: tuple[str, ...]
    intercept: FiniteFloat
    weights: tuple[FiniteFloat, ...]
    interval_half_width: FiniteFloat = Field(gt=0)
    sites_by_predecessor: dict[Predecessor, tuple[tuple[FiniteFloat, FiniteFloat], ...]]
    promoted: bool
    validation: dict

    @model_validator(mode="after")
    def check_artifact(self):
        if (
            self.feature_names != FEATURES
            or len(self.weights) != len(FEATURES)
            or set(self.sites_by_predecessor) != {"Soy", "Wheat"}
            or self.version != artifact_version(self.model_dump(mode="json", exclude={"version"}))
        ):
            raise ValueError("The rotation model artifact is invalid.")
        if self.promoted:
            checks = self.validation.get("gate")
            if (
                not isinstance(checks, dict)
                or set(checks) != RELEASE_CHECKS
                or not all(result is True for result in checks.values())
            ):
                raise ValueError("The rotation model has no passing release evidence.")
        return self

    def supports_location(self, latitude: float, longitude: float) -> bool:
        return 38 <= latitude <= 45.5 and -96 <= longitude <= -81

    def predict(self, latitude: float, longitude: float, preceding_crop: Predecessor) -> float:
        features = effect_features(latitude, longitude, preceding_crop)
        return self.intercept + sum(
            weight * value for weight, value in zip(self.weights, features, strict=True)
        )

    def estimate(
        self,
        *,
        latitude: float,
        longitude: float,
        crop: str,
        preceding_crop: str | None,
        crop_sequence: tuple[str, ...],
    ) -> RotationYieldEstimate | None:
        if not (
            isfinite(latitude)
            and isfinite(longitude)
            and self.supports_location(latitude, longitude)
            and normalize_crop(crop) == "Maize"
        ):
            return None
        previous = normalize_crop(preceding_crop)
        sequence = tuple(normalize_crop(item) for item in crop_sequence)
        if not sequence or None in sequence or "Maize" not in sequence:
            return None
        if previous == "Maize" and set(sequence) == {"Maize"}:
            return RotationYieldEstimate(0, 0, 0)
        if previous not in ("Soy", "Wheat") or previous not in sequence:
            return None
        crops = set(sequence)
        three_crop_cycle = len(sequence) == 3 and crops == {"Maize", "Soy", "Wheat"}
        alternating_pair = (
            previous == "Soy"
            and crops == {"Maize", "Soy"}
            and all(left != right for left, right in zip(sequence, sequence[1:]))
        )
        if not (three_crop_cycle or alternating_pair):
            return None
        if not any(
            site_distance(latitude, longitude, *site) <= 200
            for site in self.sites_by_predecessor[previous]
        ):
            return None
        value = self.predict(latitude, longitude, previous)
        width = self.interval_half_width
        return RotationYieldEstimate(
            round(value, 3), round(value - width, 3), round(value + width, 3)
        )


@lru_cache(maxsize=1)
def get_rotation_yield_model() -> RotationYieldModel | None:
    try:
        content = files(__package__).joinpath(ARTIFACT).read_bytes()
        return RotationYieldModel.model_validate_json(content)
    except FileNotFoundError:
        return None
    except (OSError, ValidationError) as error:
        raise RotationYieldModelUnavailable("The rotation model artifact is invalid.") from error
