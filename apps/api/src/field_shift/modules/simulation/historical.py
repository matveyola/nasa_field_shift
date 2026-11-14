from datetime import UTC, date, datetime, timedelta
from math import isfinite

from field_shift_science.crops.fao56 import (
    FAO56_CROP_COEFFICIENTS,
    FAO56_WATER_STRESS,
    crop_water_parameters,
)
from field_shift_science.fields.schemas import Polygon
from field_shift_science.simulation.schemas import (
    HistoricalProvenance,
    SourceReference,
    WaterResult,
    WaterSource,
)
from field_shift_science.simulation.water_balance import (
    FAO56_BUCKET_VERSION,
    REFERENCE_FIELD_CAPACITY,
    REFERENCE_WILTING_POINT,
    WaterInputs,
    fao56_days,
)
from field_shift_science.weather.historical import (
    ARCHIVE_LAG_DAYS,
    ERA5_DATASET,
    ERA5_SPATIAL_RESOLUTION_DEGREES,
    OPEN_METEO_DOCUMENTATION,
    HistoricalWeatherProvider,
    WeatherUnavailableError,
)
from field_shift_science.weather.scenarios import Scenario
from pydantic import ValidationError

MAX_HISTORICAL_DAYS = 62
_RAINFALL_MULTIPLIER = {
    Scenario.BASELINE: 1.0,
    Scenario.DRY_YEAR: 0.5,
    Scenario.LESS_RAIN: 0.7,
}


class HistoricalInputError(ValueError):
    """An input cannot be evaluated by the historical water model."""


def historical_window(
    season: int, start: date | None, end: date | None, *, today: date | None = None
) -> tuple[date, date]:
    if start is None and end is None:
        start, end = date(season, 8, 1), date(season, 8, 10)
    if start is None or end is None:
        raise HistoricalInputError("Provide both start_date and end_date.")
    if not 0 <= (end - start).days < MAX_HISTORICAL_DAYS:
        raise HistoricalInputError(f"Choose a date range of 1 to {MAX_HISTORICAL_DAYS} days.")
    if start < date(1940, 1, 2):
        raise HistoricalInputError(
            "Historical weather requires a start date on or after 1940-01-02."
        )
    latest = (today if today is not None else datetime.now(UTC).date()) - timedelta(
        days=ARCHIVE_LAG_DAYS
    )
    if end > latest:
        raise HistoricalInputError(
            f"Choose a historical end date on or before {latest.isoformat()}."
        )
    return start, end


def _centroid(geometry: dict | None) -> tuple[float, float]:
    try:
        ring = Polygon.model_validate(geometry).coordinates[0]
    except ValidationError:
        raise HistoricalInputError(
            "A valid field polygon is required for weather sampling."
        ) from None

    origin_x, origin_y = ring[0]
    offsets = [(x - origin_x, y - origin_y) for x, y in ring]
    signed_area = latitude = longitude = 0.0
    for (ax, ay), (bx, by) in zip(offsets[:-1], offsets[1:], strict=True):
        cross = ax * by - ay * bx
        signed_area += cross
        longitude += (ax + bx) * cross
        latitude += (ay + by) * cross
    return origin_y + latitude / (3 * signed_area), origin_x + longitude / (3 * signed_area)


def _weighted_moisture(layers: tuple[float, float, float], depth_m: float) -> float:
    thicknesses = (
        min(depth_m, 0.07),
        max(0.0, min(depth_m, 0.28) - 0.07),
        max(0.0, depth_m - 0.28),
    )
    return (
        sum(value * thickness for value, thickness in zip(layers, thicknesses, strict=True))
        / depth_m
    )


class HistoricalWaterModel:
    """A single-coefficient FAO-56 bucket driven by archived ERA5 weather."""

    def __init__(self, weather: HistoricalWeatherProvider) -> None:
        self.weather = weather

    def run(self, inputs: WaterInputs) -> WaterResult:
        start, end = historical_window(inputs.season, inputs.start_date, inputs.end_date)
        crop = crop_water_parameters(inputs.crop)
        if crop is None:
            raise HistoricalInputError(
                "Historical water balance currently supports maize, soy and wheat."
            )
        if not isfinite(inputs.irrigation_mm) or not 0 <= inputs.irrigation_mm <= 500:
            raise HistoricalInputError("Irrigation must be between 0 and 500 mm.")
        if (inputs.flowering_start is None) != (inputs.flowering_end is None):
            raise HistoricalInputError("Provide both flowering_start and flowering_end.")
        if (
            inputs.flowering_start is not None
            and inputs.flowering_end is not None
            and (
                inputs.flowering_end < inputs.flowering_start
                or inputs.flowering_end < start
                or inputs.flowering_start > end
            )
        ):
            raise HistoricalInputError(
                "The flowering interval must be ordered and overlap the run."
            )

        capacity = round(
            1000 * (REFERENCE_FIELD_CAPACITY - REFERENCE_WILTING_POINT) * crop.root_depth_m, 4
        )
        threshold = round((1 - crop.depletion_fraction) * capacity, 4)
        reserve = inputs.initial_reserve_mm
        if reserve is not None and (not isfinite(reserve) or not 0 <= reserve <= capacity):
            raise HistoricalInputError(f"Initial reserve must be between 0 and {capacity:g} mm.")
        latitude, longitude = _centroid(inputs.geometry)
        archive = self.weather.load(latitude, longitude, start, end)
        dates = tuple(start + timedelta(days=index) for index in range((end - start).days + 1))
        if tuple(day.date for day in archive.days) != dates:
            raise WeatherUnavailableError("The historical weather archive returned missing dates.")

        initial_source = "user_input"
        theta = None
        if reserve is None:
            if archive.prior_day_moisture is None:
                raise WeatherUnavailableError(
                    "Prior-day ERA5 soil moisture is missing. "
                    "Provide an initial reserve or use another date."
                )
            initial_source = "era5_prior_day"
            theta = _weighted_moisture(archive.prior_day_moisture, crop.root_depth_m)
            reserve = min(
                capacity,
                max(0.0, 1000 * (theta - REFERENCE_WILTING_POINT) * crop.root_depth_m),
            )
        initial = round(reserve, 4)

        multiplier = _RAINFALL_MULTIPLIER[inputs.scenario]
        days = fao56_days(
            archive.days,
            initial_mm=initial,
            capacity_mm=capacity,
            threshold_mm=threshold,
            coefficient=crop.midseason_coefficient,
            rainfall_multiplier=multiplier,
            irrigation_mm=inputs.irrigation_mm,
        )
        stress = [day.date for day in days if day.reserve_mm < threshold]

        return WaterResult(
            field_id=inputs.field_id,
            season=inputs.season,
            scenario=inputs.scenario,
            demo=False,
            source=WaterSource.HISTORICAL,
            provenance=HistoricalProvenance(
                weather_source="Open-Meteo Historical Weather API",
                weather_model="ECMWF ERA5",
                weather_url=OPEN_METEO_DOCUMENTATION,
                latitude=round(latitude, 6),
                longitude=round(longitude, 6),
                grid_latitude=archive.grid_latitude,
                grid_longitude=archive.grid_longitude,
                grid_resolution_degrees=ERA5_SPATIAL_RESOLUTION_DEGREES,
                timezone=archive.timezone,
                start_date=start,
                end_date=end,
                rainfall_multiplier=multiplier,
                initial_reserve_mm=initial,
                initial_reserve_source=initial_source,
                initial_moisture_date=start - timedelta(days=1) if theta is not None else None,
                initial_theta_m3_m3=round(theta, 5) if theta is not None else None,
                capacity_mm=capacity,
                root_depth_m=crop.root_depth_m,
                field_capacity=REFERENCE_FIELD_CAPACITY,
                wilting_point=REFERENCE_WILTING_POINT,
                crop_coefficient=crop.midseason_coefficient,
                depletion_fraction=crop.depletion_fraction,
                model_version=FAO56_BUCKET_VERSION,
                limitations=[
                    "ERA5 supplies historical grid estimates sampled at the field centroid.",
                    "Capacity assumes uncalibrated loam and fixed crop rooting depth.",
                    "Midseason Kc and depletion fraction are fixed over the run.",
                    "The balance assumes full infiltration, zero snowmelt and zero capillary rise.",
                    "Irrigation is a single first-day net depth. Stress uses closing reserve.",
                    "ERA5 initialization uses depth-weighted moisture from the preceding day.",
                ],
                sources=[
                    SourceReference(
                        label="Open-Meteo Historical Weather API", url=OPEN_METEO_DOCUMENTATION
                    ),
                    SourceReference(
                        label="Copernicus Climate Change Service ERA5", url=ERA5_DATASET
                    ),
                    SourceReference(
                        label="FAO-56 Chapter 6, Table 12", url=FAO56_CROP_COEFFICIENTS
                    ),
                    SourceReference(
                        label="FAO-56 Chapter 8, Table 22 and water balance", url=FAO56_WATER_STRESS
                    ),
                ],
            ),
            threshold_mm=threshold,
            flowering_start=inputs.flowering_start,
            flowering_end=inputs.flowering_end,
            first_stress_date=stress[0] if stress else None,
            min_reserve_mm=min(day.reserve_mm for day in days),
            stress_days=len(stress),
            days=days,
        )
