from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from os import getenv

from field_shift_science.fields.schemas import Polygon
from field_shift_science.geo_ml.era5_land import (
    GRID_STEP,
    UNIT,
    MoistureSourceError,
    field_location,
    grid_cells,
    latest_archive_day,
)
from field_shift_science.geo_ml.moisture_features import fetch_moisture_features
from field_shift_science.geo_ml.moisture_model import (
    MoistureModel,
    MoistureModelUnavailable,
    MoistureModelV2,
    get_moisture_model,
    get_moisture_model_v2,
)
from field_shift_science.geo_ml.schemas import GeoResult, MoistureInterval, MoistureSource
from field_shift_science.geo_ml.ssurgo import SSURGO_SOURCE, SoilSurveyError, fetch_soil_profile

THAWED_LOOKBACK_DAYS = 7
TRAINING_DATA_URL = "https://www.ncei.noaa.gov/access/crn/products.html"


def experimental_enabled() -> bool:
    return getenv("MOISTURE_ENABLE_EXPERIMENTAL", "0") == "1"


def active_moisture_model(fallback: Callable[[], MoistureModel]) -> MoistureModel | MoistureModelV2:
    try:
        candidate = get_moisture_model_v2()
    except MoistureModelUnavailable:
        if experimental_enabled() and getenv("MOISTURE_MODEL_PREVIEW", "v1") == "v2":
            raise
        return fallback()
    if candidate.promoted or (
        experimental_enabled() and getenv("MOISTURE_MODEL_PREVIEW", "v1") == "v2"
    ):
        return candidate
    return fallback()


def latest_supported_day(
    archive_day: date, supports_date: Callable[[date], bool], season: int | None = None
) -> date | None:
    earliest = archive_day - timedelta(days=365)
    if season is not None:
        earliest = date(season, 1, 1)
        archive_day = min(archive_day, date(season, 12, 31))
    for offset in range((archive_day - earliest).days + 1):
        day = archive_day - timedelta(days=offset)
        if supports_date(day):
            return day
    return None


def resolve_ml_day(
    season: int,
    requested_day: date | None,
    submitted_at: datetime,
    supports_date: Callable[[date], bool],
) -> date:
    latest = latest_archive_day(submitted_at.astimezone(UTC).date())
    if season > latest.year or (
        requested_day is not None and (requested_day.year != season or requested_day > latest)
    ):
        raise ValueError(
            "Choose a date within the selected season and at least six days in the past."
        )
    day = requested_day or latest_supported_day(latest, supports_date, season)
    if day is None or not supports_date(day):
        raise ValueError("The soil moisture model does not cover this month.")
    return day


def run_ml_layer(
    field_id: str,
    season: int,
    geometry: Polygon,
    requested_day: date | None,
    submitted_at: datetime,
) -> GeoResult:
    model = active_moisture_model(get_moisture_model)
    if not model.promoted and not experimental_enabled():
        raise ValueError("The experimental soil moisture model is disabled for this deployment.")

    latitude, longitude = field_location(geometry)
    if not model.supports_location(latitude, longitude):
        raise ValueError("This field is outside the validated soil moisture model region.")

    day = resolve_ml_day(season, requested_day, submitted_at, model.supports_date)
    start = (
        day
        if requested_day is not None
        else max(date(season, 1, 1), day - timedelta(days=THAWED_LOOKBACK_DAYS - 1))
    )
    frame = fetch_moisture_features(
        latitude, longitude, start, day, api_key=getenv("OPEN_METEO_API_KEY")
    )
    if not frame.days or frame.days[-1].date != day:
        raise MoistureSourceError(
            "The archive does not have complete features for the requested day."
        )
    features = next(
        (
            item
            for item in reversed(frame.days)
            if not item.is_frozen and model.supports_date(item.date)
        ),
        None,
    )
    if features is None:
        raise ValueError("The soil moisture model requires a supported day with unfrozen ground.")

    soil = None
    if isinstance(model, MoistureModelV2):
        try:
            soil = fetch_soil_profile(latitude, longitude)
        except SoilSurveyError:
            raise MoistureSourceError("USDA soil survey is temporarily unavailable.") from None
        if soil is None or soil.sand is None or soil.clay is None:
            raise ValueError("A complete SSURGO profile is required for this field.")
        prediction = model.predict(features, soil)
    else:
        prediction = model.predict(features)
    value = round(prediction.value, 3)
    external_validation = model.validation.get("independent_farms") or {}
    return GeoResult(
        field_id=field_id,
        season=season,
        demo=False,
        date=features.date,
        depth_cm=(5, 5),
        unit=UNIT,
        minimum=value,
        maximum=value,
        cells=grid_cells(frame.grid_latitude, frame.grid_longitude, value),
        source=MoistureSource(
            name="Field Shift soil moisture",
            provider=(
                "Field Shift · ERA5-Land, SSURGO and field sensors"
                if soil is not None
                else "Field Shift · ERA5-Land and field sensors"
            ),
            url=TRAINING_DATA_URL,
            kind="ml",
            resolution_degrees=GRID_STEP,
            grid_latitude=frame.grid_latitude,
            grid_longitude=frame.grid_longitude,
            sample_count=1,
            requested_latitude=round(latitude, 6),
            requested_longitude=round(longitude, 6),
            model_version=model.version,
            experimental=not model.promoted,
            soil_profile=soil,
            soil_source_url=SSURGO_SOURCE if soil is not None else None,
            interval=MoistureInterval(
                lower=round(prediction.lower, 3),
                upper=round(prediction.upper, 3),
                coverage=model.interval_level,
                empirical_coverage=external_validation.get("interval_coverage"),
            ),
            generated_at=datetime.now(UTC),
        ),
    )
