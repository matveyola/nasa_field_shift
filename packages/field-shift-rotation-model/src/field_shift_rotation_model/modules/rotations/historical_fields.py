from datetime import date, timedelta
from functools import lru_cache
from importlib.resources import files
from itertools import pairwise
from typing import Annotated
from urllib.parse import urlencode

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    field_serializer,
    field_validator,
    model_validator,
)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
ARCHIVE_DOCS = "https://open-meteo.com/en/docs/historical-weather-api"


_WEATHER_UNITS = {
    "rain_sum": "mm",
    "temperature_2m_min": "°C",
    "temperature_2m_max": "°C",
    "et0_fao_evapotranspiration": "mm",
}
_MOISTURE_UNITS = dict.fromkeys(
    (
        "soil_moisture_0_to_7cm_mean",
        "soil_moisture_7_to_28cm_mean",
        "soil_moisture_28_to_100cm_mean",
    ),
    "m³/m³",
)
_DAILY_UNITS = {"time": "iso8601", **_WEATHER_UNITS, **_MOISTURE_UNITS}
_WEATHER_FIELDS = ("date", "rain_mm", "low_c", "high_c", "reference_et0_mm")

Latitude = Annotated[FiniteFloat, Field(ge=-90, le=90)]
Longitude = Annotated[FiniteFloat, Field(ge=-180, le=180)]
Fraction = Annotated[FiniteFloat, Field(ge=0, le=1)]


def archive_url(latitude: float, longitude: float, start_year: int, end_year: int) -> str:
    return f"{ARCHIVE_URL}?" + urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": date(start_year, 4, 30),
            "end_date": date(end_year, 9, 30),
            "models": "era5_seamless",
            "timezone": "GMT",
            "cell_selection": "nearest",
            "daily": ",".join((*_WEATHER_UNITS, *_MOISTURE_UNITS)),
        }
    )


class SoilLayer(BaseModel):
    top_m: Annotated[FiniteFloat, Field(ge=0)]
    bottom_m: Annotated[FiniteFloat, Field(gt=0)]
    field_capacity: Fraction
    wilting_point: Fraction

    @model_validator(mode="after")
    def physical_layer(self):
        if self.bottom_m <= self.top_m or self.field_capacity <= self.wilting_point:
            raise ValueError("Soil layer depths or water retention values are invalid.")
        return self


class WeatherDay(BaseModel):
    date: date
    rain_mm: Annotated[FiniteFloat, Field(ge=0)]
    low_c: Annotated[FiniteFloat, Field(ge=-90, le=70)]
    high_c: Annotated[FiniteFloat, Field(ge=-90, le=70)]
    reference_et0_mm: Annotated[FiniteFloat, Field(ge=0)]

    @model_validator(mode="after")
    def ordered_temperatures(self):
        if self.low_c > self.high_c:
            raise ValueError("Daily minimum temperature exceeds the maximum.")
        return self


class SeasonData(BaseModel):
    initial_theta: tuple[Fraction, Fraction, Fraction]
    days: tuple[WeatherDay, ...]

    @field_validator("days", mode="before")
    @classmethod
    def parse_weather_rows(cls, rows):
        if not isinstance(rows, (list, tuple)):
            return rows
        return [
            dict(zip(_WEATHER_FIELDS, row, strict=True)) if isinstance(row, (list, tuple)) else row
            for row in rows
        ]

    @field_serializer("days")
    def compact_weather_rows(self, days):
        return [
            [
                day.date.isoformat(),
                day.rain_mm,
                day.low_c,
                day.high_c,
                day.reference_et0_mm,
            ]
            for day in days
        ]


class FieldConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,47}$")
    name: str = Field(min_length=1, max_length=120)
    latitude: Latitude
    longitude: Longitude
    location_source_url: str | None = None
    soil_source_url: str = Field(min_length=1)
    soil_layers: tuple[SoilLayer, ...] = Field(min_length=1)

    @field_validator("soil_layers")
    @classmethod
    def contiguous_soil_layers(cls, layers):
        top = 0.0
        for layer in layers:
            if abs(layer.top_m - top) > 1e-6:
                raise ValueError("Soil layers must be ordered and contiguous from the surface.")
            top = layer.bottom_m
        if top < 1:
            raise ValueError("Soil layers must cover at least the first metre.")
        return layers


class FieldDataset(FieldConfig):
    weather_source_url: str = Field(min_length=1)
    grid_latitude: Latitude
    grid_longitude: Longitude
    seasons: dict[int, SeasonData]

    @model_validator(mode="after")
    def complete_seasons(self):
        offset_lon = (self.grid_longitude - self.longitude + 180) % 360 - 180
        if abs(self.grid_latitude - self.latitude) > 0.2 or abs(offset_lon) > 0.2:
            raise ValueError("The archive grid coordinate is too far from the requested field.")
        years = sorted(self.seasons)
        if len(years) < 4 or years != list(range(years[0], years[-1] + 1)):
            raise ValueError("At least four consecutive complete years are required.")
        for year, season in self.seasons.items():
            expected = tuple(date(year, 5, 1) + timedelta(days=index) for index in range(153))
            if tuple(day.date for day in season.days) != expected:
                raise ValueError(f"Season {year} must contain each day from May 1 through Sep 30.")
        return self


def build_dataset(
    config: dict | FieldConfig,
    payload: dict,
    weather_source_url: str,
) -> FieldDataset:
    """Validate and preserve archive values for each complete growing season."""
    site = FieldConfig.model_validate(config)
    try:
        if payload["utc_offset_seconds"] != 0 or payload["timezone"] not in (
            "GMT",
            "UTC",
            "Etc/GMT",
            "Etc/UTC",
        ):
            raise ValueError("The archive response must use UTC.")
        units = payload["daily_units"]
        if not isinstance(units, dict) or any(
            units.get(key) != unit for key, unit in _DAILY_UNITS.items()
        ):
            raise ValueError("The archive response uses unexpected units or missing columns.")
        daily = payload["daily"]
        dates = tuple(date.fromisoformat(value) for value in daily["time"])
        if not dates or any(b - a != timedelta(days=1) for a, b in pairwise(dates)):
            raise ValueError("Archive dates must be unique, consecutive, and chronological.")
        weather = dict(
            zip(
                dates,
                zip(*(daily[key] for key in _WEATHER_UNITS), strict=True),
                strict=True,
            )
        )
        moisture = dict(
            zip(
                dates,
                zip(*(daily[key] for key in _MOISTURE_UNITS), strict=True),
                strict=True,
            )
        )
        seasons = {}
        for year in range(dates[0].year, dates[-1].year + 1):
            first = date(year, 5, 1)
            season_days = (first + timedelta(days=index) for index in range(153))
            seasons[year] = {
                "initial_theta": moisture[date(year, 4, 30)],
                "days": [(day.isoformat(), *weather[day]) for day in season_days],
            }
        return FieldDataset(
            **site.model_dump(),
            weather_source_url=weather_source_url,
            grid_latitude=payload["latitude"],
            grid_longitude=payload["longitude"],
            seasons=seasons,
        )
    except (KeyError, TypeError) as error:
        raise ValueError(f"Missing or malformed historical archive data: {error}") from error


def bundled_field_files():
    bundled = files(__package__).joinpath("artifacts").joinpath("historical-fields")
    if bundled.is_dir():
        yield from (path for path in bundled.iterdir() if path.name.endswith(".json"))


@lru_cache(maxsize=1)
def load_fields() -> dict[str, FieldDataset]:
    """Load validated snapshots once; restart the API after importing a local field."""
    loaded = {}
    for paths in (bundled_field_files(),):
        for path in sorted(paths, key=lambda item: item.name):
            try:
                dataset = FieldDataset.model_validate_json(path.read_text(encoding="utf-8"))
            except ValueError as error:
                raise ValueError(f"Invalid field dataset in {path.name}: {error}") from error
            if dataset.id in loaded:
                raise ValueError(
                    f"Duplicate field ID {dataset.id}. Local fields cannot replace bundled data."
                )
            loaded[dataset.id] = dataset
    return loaded
