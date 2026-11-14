from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files
from json import dumps
from math import asin, cos, isfinite, radians, sin, sqrt
from struct import pack, unpack
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, Field, FiniteFloat, ValidationError, model_validator

from field_shift.modules.geo_ml.moisture_features import FEATURE_NAMES, MoistureFeatures

if TYPE_CHECKING:
    from field_shift.modules.geo_ml.ssurgo import SoilProfile

ARTIFACT_NAME = "artifacts/moisture-v1.json"
ARTIFACT_SCHEMA = 1
V2_ARTIFACT_NAME = "artifacts/moisture-v2.json"
V2_ARTIFACT_SCHEMA = 2
V2_PROMOTION_CHECKS = frozenset(
    {
        "enough_farms",
        "aggregate_gain",
        "wins_most_farms",
        "no_material_farm_degradation",
        "interval_coverage",
        "regional_sanity",
    }
)
SOIL_ATTRIBUTES = (
    "sand",
    "silt",
    "clay",
    "organic_matter",
    "bulk_density",
    "field_capacity",
    "wilting_point",
)
V2_FEATURE_NAMES = FEATURE_NAMES + (
    *SOIL_ATTRIBUTES,
    *(f"{attribute}_missing" for attribute in SOIL_ATTRIBUTES),
    "surface_by_clay",
    "surface_by_sand",
    "surface_by_field_capacity",
)


class MoistureModelUnavailable(RuntimeError):
    """The packaged model is missing, invalid, or incompatible with the feature contract."""


class _Coverage(BaseModel, frozen=True):
    bounds: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
    station_locations: tuple[tuple[FiniteFloat, FiniteFloat], ...] = Field(min_length=1)
    max_station_distance_km: FiniteFloat = Field(gt=0, le=500)
    validated_months: tuple[Annotated[int, Field(ge=1, le=12)], ...]


class _LinearFit(BaseModel, frozen=True):
    kind: Literal["linear"]
    intercept: FiniteFloat
    coefficients: tuple[FiniteFloat, ...]

    def correction(self, features: tuple[float, ...]) -> float:
        return self.intercept + sum(
            weight * value for weight, value in zip(self.coefficients, features, strict=True)
        )


class _Tree(BaseModel, frozen=True):
    nodes: tuple[tuple[int, FiniteFloat, int, int, FiniteFloat], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_indices(self) -> "_Tree":
        # Children always follow their parent in the exported pre-order traversal.
        for index, (feature, _, left, right, _) in enumerate(self.nodes):
            if feature == -1 and left == right == -1:
                continue
            if feature < 0 or not index < left < right < len(self.nodes):
                raise ValueError("Invalid regression tree node")
        return self

    def predict(self, features: tuple[float, ...]) -> float:
        index = 0
        while True:
            feature, threshold, left, right, value = self.nodes[index]
            if feature == -1:
                return value
            index = left if features[feature] <= threshold else right


class _TreeFit(BaseModel, frozen=True):
    kind: Literal["trees"]
    intercept: FiniteFloat
    learning_rate: FiniteFloat = Field(gt=0, le=1)
    trees: tuple[_Tree, ...] = Field(min_length=1, max_length=250)

    def correction(self, features: tuple[float, ...]) -> float:
        # scikit-learn casts incoming tree features to float32 before comparing thresholds.
        format_string = f"{len(features)}f"
        tree_features = unpack(format_string, pack(format_string, *features))
        return self.intercept + self.learning_rate * sum(
            tree.predict(tree_features) for tree in self.trees
        )


class _ModelArtifact(BaseModel, frozen=True):
    version: str = Field(pattern=r"^fieldshift-vwc-[0-9a-f]{12}$")
    feature_names: tuple[str, ...]
    promoted: bool
    fit: Annotated[_LinearFit | _TreeFit, Field(discriminator="kind")]
    coverage: _Coverage
    interval_level: FiniteFloat = Field(gt=0, lt=1)
    interval_half_width: FiniteFloat = Field(gt=0, le=1)
    validation: dict

    @model_validator(mode="after")
    def validate_fit_and_identity(self) -> "_ModelArtifact":
        feature_count = len(self.feature_names)
        if isinstance(self.fit, _LinearFit) and len(self.fit.coefficients) != feature_count:
            raise ValueError("The coefficient count does not match the feature contract")
        if isinstance(self.fit, _TreeFit) and any(
            node[0] >= feature_count for tree in self.fit.trees for node in tree.nodes
        ):
            raise ValueError("The regression tree uses an invalid feature index")
        if self.version != artifact_version(self.model_dump(mode="json", exclude={"version"})):
            raise ValueError("Model artifact version does not match its contents")
        return self


class _Artifact(_ModelArtifact):
    artifact_schema: Literal[1]

    @model_validator(mode="after")
    def validate_features(self) -> "_Artifact":
        if self.feature_names != FEATURE_NAMES:
            raise ValueError("Model and runtime feature order differ")
        return self


class _V2Artifact(_ModelArtifact):
    artifact_schema: Literal[2]
    soil_medians: tuple[FiniteFloat, ...]

    @model_validator(mode="after")
    def validate_soil_features(self) -> "_V2Artifact":
        if self.feature_names != V2_FEATURE_NAMES or len(self.soil_medians) != len(SOIL_ATTRIBUTES):
            raise ValueError("Model and runtime soil feature order differ")
        report = self.validation.get("independent_farms")
        checks = report.get("promotion_checks", {}) if isinstance(report, dict) else {}
        passed = (
            isinstance(checks, dict)
            and checks.keys() == V2_PROMOTION_CHECKS
            and all(check is True for check in checks.values())
        )
        if self.promoted != passed:
            raise ValueError("The release state does not match the independent farm checks")
        return self


@dataclass(frozen=True, slots=True)
class MoisturePrediction:
    value: float
    lower: float
    upper: float


def artifact_version(payload: dict) -> str:
    encoded = dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return f"fieldshift-vwc-{sha256(encoded).hexdigest()[:12]}"


def station_distance_km(
    latitude: float, longitude: float, other_latitude: float, other_longitude: float
) -> float:
    angle = (
        sin(radians(other_latitude - latitude) / 2) ** 2
        + cos(radians(latitude))
        * cos(radians(other_latitude))
        * sin(radians(other_longitude - longitude) / 2) ** 2
    )
    return 12_742 * asin(sqrt(min(1.0, angle)))


class _MoisturePredictor:
    def __init__(self, artifact: _ModelArtifact) -> None:
        self._artifact = artifact

    @property
    def version(self) -> str:
        return self._artifact.version

    @property
    def promoted(self) -> bool:
        return self._artifact.promoted

    @property
    def validation(self) -> dict:
        return self._artifact.validation

    @property
    def coverage(self) -> dict:
        return self._artifact.coverage.model_dump(mode="json")

    @property
    def interval_level(self) -> float:
        return self._artifact.interval_level

    def supports_location(self, latitude: float, longitude: float) -> bool:
        coverage = self._artifact.coverage
        south, west, north, east = coverage.bounds
        return (
            south <= latitude <= north
            and west <= longitude <= east
            and any(
                station_distance_km(latitude, longitude, *station)
                <= coverage.max_station_distance_km
                for station in coverage.station_locations
            )
        )

    def supports_date(self, day: date) -> bool:
        return day.month in self._artifact.coverage.validated_months

    def _predict(self, day: MoistureFeatures, features: tuple[float, ...]) -> MoisturePrediction:
        if day.is_frozen:
            raise ValueError("Soil moisture estimates are unavailable for frozen or snowy days")
        if len(features) != len(self._artifact.feature_names) or not all(map(isfinite, features)):
            raise ValueError("A complete finite feature vector is required")
        value = max(0.0, min(1.0, day.era5_moisture + self._artifact.fit.correction(features)))
        width = self._artifact.interval_half_width
        return MoisturePrediction(value, max(0.0, value - width), min(1.0, value + width))


class MoistureModel(_MoisturePredictor):
    @classmethod
    def from_json(cls, encoded: bytes) -> "MoistureModel":
        try:
            return cls(_Artifact.model_validate_json(encoded))
        except ValidationError as error:
            raise MoistureModelUnavailable("The packaged moisture model is invalid") from error

    def predict(self, day: MoistureFeatures) -> MoisturePrediction:
        return self._predict(day, day.values)


def soil_moisture_features(
    day: MoistureFeatures, soil: "SoilProfile", medians: tuple[float, ...]
) -> tuple[float, ...]:
    if len(day.values) != len(FEATURE_NAMES):
        raise ValueError("A complete finite feature vector is required")
    if soil.sand is None or soil.clay is None:
        raise ValueError("A soil profile with sand and clay is required")
    values = tuple(getattr(soil, attribute) for attribute in SOIL_ATTRIBUTES)
    present = tuple(
        median if value is None else value for value, median in zip(values, medians, strict=True)
    )
    sand, _, clay, _, _, field_capacity, _ = present
    surface = day.era5_moisture
    return (
        *day.values,
        *present,
        *(float(value is None) for value in values),
        surface * clay,
        surface * sand,
        surface * field_capacity,
    )


class MoistureModelV2(_MoisturePredictor):
    def __init__(self, artifact: _V2Artifact) -> None:
        super().__init__(artifact)
        self._soil_medians = artifact.soil_medians

    @classmethod
    def from_json(cls, encoded: bytes) -> "MoistureModelV2":
        try:
            return cls(_V2Artifact.model_validate_json(encoded))
        except ValidationError as error:
            raise MoistureModelUnavailable(
                "The packaged soil-conditioned model is invalid"
            ) from error

    def predict(self, day: MoistureFeatures, soil: "SoilProfile") -> MoisturePrediction:
        return self._predict(day, soil_moisture_features(day, soil, self._soil_medians))


def _read_artifact(name: str) -> bytes:
    try:
        return files(__package__).joinpath(name).read_bytes()
    except OSError as error:
        raise MoistureModelUnavailable("The packaged moisture model is unavailable") from error


@lru_cache(maxsize=1)
def get_moisture_model() -> MoistureModel:
    return MoistureModel.from_json(_read_artifact(ARTIFACT_NAME))


@lru_cache(maxsize=1)
def get_moisture_model_v2() -> MoistureModelV2:
    return MoistureModelV2.from_json(_read_artifact(V2_ARTIFACT_NAME))
