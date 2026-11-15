"""Project stored field evidence onto the agent's v0.1 wire contract."""

from datetime import UTC, date, datetime, timedelta
from math import fsum, isfinite
from typing import Literal
from urllib.parse import urlsplit

from field_shift_science.fields.geometry import area_hectares
from field_shift_science.fields.schemas import FieldRead
from field_shift_science.geo_ml.schemas import GeoResult
from field_shift_science.simulation.schemas import WaterDay, WaterForecast, WaterResult, WaterSource
from field_shift_science.weather.scenarios import Scenario
from shapely.geometry import Polygon as ShapelyPolygon

from field_shift.modules.farm.schemas import ScoutingEvidence
from field_shift.modules.imagery.vegetation_schemas import METHOD_VERSION, VegetationSeason
from field_shift.modules.terrain.schemas import TerrainResult

NDVI_LAYER = "l_ndvi_variability"
VEGETATION_CHART = "c_vegetation"
FORECAST_CHART = "c_water_forecast"
HISTORICAL_CHART = "c_water_season"


def _source(provider: str, product: str, version: str, url: str) -> dict:
    source = {"provider": provider[:300], "product_id": product[:300], "version": version[:300]}
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not (parsed.username or parsed.password)
            and len(url) <= 2083
        ):
            source["url"] = url
    except ValueError:
        pass
    return source


def _range(start: date, end: date) -> dict:
    return {"start": start.isoformat(), "end": end.isoformat()}


def _provenance(
    source: dict,
    method: str,
    start: date | None,
    end: date | None,
    retrieved: datetime,
    *,
    resolution_m: float | None = None,
    kind: str = "deterministic_calc",
) -> dict:
    provenance = {
        "kind": kind,
        "data_status": "cached",
        "source": source,
        "method": method[:300],
        "retrieved_at": retrieved.astimezone(UTC).isoformat(),
    }
    if start is not None and end is not None:
        provenance["temporal"] = _range(start, end)
    if resolution_m is not None and isfinite(resolution_m) and resolution_m > 0:
        provenance["spatial"] = {"resolution_m": resolution_m}
    return provenance


# Shown as stored: no more digits than the measurement or model supports (142.3 mm, not 142.308 mm).
# A 30 m surface model is not accurate to the metre; ERA5-Land moisture is not to 1e-4 m3/m3.
DIGITS = {"mm": 1, "%": 1, "1": 2, "d": 0, "m": 0, "m3/m3": 3}


def _fact(domain: str, metric: str, value: float, unit: str, provenance: dict) -> dict:
    return {
        "id": f"f_{metric}",
        "domain": domain,
        "label_key": f"fact.{metric}",
        "quantity": {"value": round(value, DIGITS.get(unit, 2)), "unit": unit},
        "provenance": provenance,
    }


def _retrieved(saved: datetime | None, now: datetime) -> datetime:
    """When the host computed the stored record; the read time if that is missing or implausible."""
    return saved if saved is not None and saved.tzinfo is not None and saved <= now else now


def _series(metric: str, label: str, unit: str, provenance: dict) -> dict:
    return {
        "metric_id": metric,
        "label_key": f"fact.{label}",
        "unit": unit,
        "range": provenance["temporal"],
        "provenance": provenance,
    }


def _valid_water_days(days: list[WaterDay], threshold: float) -> bool:
    return (
        bool(days)
        and isfinite(threshold)
        and threshold >= 0
        and all(
            isfinite(day.reserve_mm)
            and day.reserve_mm >= 0
            and isfinite(day.rain_mm)
            and day.rain_mm >= 0
            for day in days
        )
        and all(right.date - left.date == timedelta(days=1) for left, right in zip(days, days[1:]))
    )


def _add_water(
    context: dict,
    days: list[WaterDay],
    threshold: float,
    provenance: dict,
    *,
    chart: str,
) -> None:
    prefix = "forecast" if chart == FORECAST_CHART else "historical"
    minimum = f"{prefix}_min_root_zone_reserve"
    precipitation = f"{prefix}_precipitation_total"
    context["facts"].extend(
        (
            _fact("water", minimum, min(day.reserve_mm for day in days), "mm", provenance),
            _fact("water", "water_stress_threshold", threshold, "mm", provenance),
            _fact(
                "water",
                "water_stress_days",
                sum(day.reserve_mm < threshold for day in days),
                "d",
                provenance,
            ),
            _fact("water", precipitation, fsum(day.rain_mm for day in days), "mm", provenance),
        )
    )
    context["timeseries_index"].extend(
        (
            _series("m_rootzone_reserve", f"{prefix}_root_zone_reserve", "mm", provenance),
            _series("m_precip", precipitation, "mm", provenance),
        )
    )
    context["available_charts"].append(
        {"chart_id": chart, "metric_ids": ["m_rootzone_reserve", "m_precip"]}
    )


def _add_forecast(context: dict, forecast: WaterForecast, season: int, now: datetime) -> None:
    today = now.date()
    if (
        forecast.field_id != context["field_id"]
        or forecast.season != season
        or season != today.year
        or forecast.initialized_at.tzinfo is None
        or forecast.generated_at.tzinfo is None
        or forecast.initialized_at > now
        or forecast.generated_at > now
        or forecast.generated_at < forecast.initialized_at
        or now - forecast.initialized_at > timedelta(days=3)
    ):
        return
    days: list[WaterDay] = sorted(
        (day for day in forecast.days if day.date >= today), key=lambda day: day.date
    )
    if not _valid_water_days(days, forecast.threshold_mm) or days[0].date != today:
        return
    if forecast.applicability is None:
        applicability = "Applicability unknown: no stored shallow-soil diagnostic. "
    elif forecast.applicability.status == "review_required":
        applicability = "review_required: shallow 0–7 cm daily-mean soil forecast <=0 °C. "
    else:
        applicability = "not_assessed: shallow 0–7 cm daily-mean soil forecast >0 °C. "
    # Put domain limits before provider text: _provenance bounds the wire method to 300 chars.
    method = (
        applicability + "Freezing/thawing/dormancy not modeled; root-zone ice, liquid water and "
        "crop suitability unassessed. Reference loam, fixed roots, zero irrigation. "
        f"{forecast.provider} forecast, "
        f"initialized {forecast.initialized_at.astimezone(UTC):%Y-%m-%d %H:%M UTC}."
    )
    provenance = _provenance(
        _source("Field Shift", "FAO-56", forecast.physical_model_version, forecast.source_url),
        method,
        days[0].date,
        days[-1].date,
        forecast.generated_at,
        resolution_m=forecast.grid_resolution_km * 1_000,
    )
    _add_water(context, days, forecast.threshold_mm, provenance, chart=FORECAST_CHART)


def _add_historical(
    context: dict, water: WaterResult, season: int, scenario: Scenario | None, now: datetime
) -> None:
    historical = water.provenance
    if (
        water.demo
        or water.source != WaterSource.HISTORICAL
        or water.field_id != context["field_id"]
        or water.season != season
        # The selected scenario must be known and identical; dry_year is never another scenario.
        or scenario is None
        or water.scenario != scenario
        or historical is None
    ):
        return
    days = sorted(water.days, key=lambda day: day.date)
    if (
        not _valid_water_days(days, water.threshold_mm)
        or days[0].date != historical.start_date
        or days[-1].date != historical.end_date
        or days[-1].date > now.date()
    ):
        return
    provenance = _provenance(
        _source("Field Shift", "FAO-56", historical.model_version, historical.weather_url),
        f"Saved historical simulation for scenario {water.scenario.value}, driven by "
        f"{historical.weather_model} archive data. Scenario rainfall multiplier "
        f"{historical.rainfall_multiplier:g}. The source link is archived weather input.",
        days[0].date,
        days[-1].date,
        now,
        resolution_m=historical.grid_resolution_degrees * 111_000,
    )
    _add_water(context, days, water.threshold_mm, provenance, chart=HISTORICAL_CHART)


def _add_vegetation(
    context: dict, result: VegetationSeason, field: FieldRead, season: int, now: datetime
) -> None:
    if (
        result.field_id != field.id
        or result.season != season
        or result.boundary_revision != field.boundary_revision
        or result.status == "unavailable"
        or result.resolution_m <= 0
    ):
        return
    observations = sorted(
        (
            sample
            for sample in result.observations
            if sample.date.year == season
            and sample.date <= now.date()
            and sample.acquired_at.tzinfo is not None
            and sample.acquired_at <= now
            and sample.acquired_at.astimezone(UTC).date() == sample.date
            and -1 <= sample.mean_ndvi <= 1
            and -1 <= sample.p10_ndvi <= sample.p90_ndvi <= 1
            and 70 <= sample.valid_percent <= 100
            and sample.pixel_count >= 10
        ),
        key=lambda sample: sample.date,
    )
    if not observations:
        return
    latest = observations[-1]
    retrieved = result.generated_at
    if retrieved is None or retrieved.tzinfo is None or not latest.acquired_at <= retrieved <= now:
        retrieved = now
    age_days = (now.date() - latest.date).days
    latest_provenance = _provenance(
        _source("Copernicus", "Sentinel-2 L2A", METHOD_VERSION, latest.catalog_url),
        f"NDVI from red and near-infrared reflectance after cloud masking; {age_days} days old. "
        "This is a sampled vegetation index. Pixels obscured by clouds are excluded.",
        latest.date,
        latest.date,
        retrieved,
        resolution_m=result.resolution_m,
    )
    context["facts"].extend(
        (
            _fact("vegetation", "ndvi_latest", latest.mean_ndvi, "1", latest_provenance),
            _fact(
                "vegetation", "ndvi_clear_coverage", latest.valid_percent, "%", latest_provenance
            ),
        )
    )
    series_provenance = _provenance(
        _source("Copernicus", "Sentinel-2 L2A", METHOD_VERSION, result.source_url),
        "Mean NDVI of valid pixels. One representative clear Sentinel-2 scene per month "
        "when available. Missing months have no observations.",
        observations[0].date,
        latest.date,
        retrieved,
        resolution_m=result.resolution_m,
    )
    context["timeseries_index"].append(_series("m_ndvi", "ndvi_latest", "1", series_provenance))
    context["available_charts"].append({"chart_id": VEGETATION_CHART, "metric_ids": ["m_ndvi"]})

    layer = result.heterogeneity
    if (
        layer is None
        or layer.date != latest.date
        or not (0 <= layer.low_area_percent <= 100 and -1 <= layer.median_ndvi <= 1)
        or not 70 <= layer.valid_percent <= 100
        or not abs(layer.low_threshold - (layer.median_ndvi - 0.15)) <= 0.001
        or not abs(layer.high_threshold - (layer.median_ndvi + 0.15)) <= 0.001
        or layer.resolution_m <= 0
    ):
        return
    layer_provenance = {
        **latest_provenance,
        "method": (
            f"NDVI zones from a {age_days}-day-old scene. Low share measures valid observed area "
            "at least 0.15 NDVI below the field median. This is an inspection cue."
        ),
    }
    context["facts"].append(
        _fact("vegetation", "ndvi_low_area_share", layer.low_area_percent, "%", layer_provenance)
    )
    context["available_layers"].append(
        {
            "layer_id": NDVI_LAYER,
            "label_key": "layer.ndvi_variability",
            "kind": "vector",
            "provenance": {
                **layer_provenance,
                "spatial": {"resolution_m": layer.resolution_m},
            },
        }
    )


def _add_surface_moisture(context: dict, result: GeoResult, season: int, now: datetime) -> None:
    source = result.source
    value = result.minimum
    if (
        result.demo
        or result.field_id != context["field_id"]
        or result.season != season
        or result.date.year != season
        or result.date > now.date()
        or tuple(result.depth_cm) != (0, 7)
        or source is None
        or source.kind != "reanalysis"
        or source.name != "ERA5-Land"
        or not (isfinite(source.resolution_degrees) and source.resolution_degrees > 0)
        # One reanalysis cell covers the field; a spread would mean another product.
        or result.maximum != value
        or not (isfinite(value) and 0 <= value <= 1)
    ):
        return
    age_days = (now.date() - result.date).days
    resolution_m = source.resolution_degrees * 111_000
    provenance = _provenance(
        # The stored record names no ERA5-Land release, so the version is not guessed.
        _source(source.provider, f"{source.name} soil_moisture_0_to_7cm", "unverified", source.url),
        f"ERA5-Land weather-model estimate (reanalysis), not a satellite or on-field measurement: "
        f"daily mean of {source.sample_count} hourly values for the 0-7 cm layer in the nearest "
        f"{source.resolution_degrees:g} degree grid cell (about {resolution_m / 1000:.0f} km); "
        f"{age_days} days old. One value for the whole area, not root-zone water.",
        result.date,
        result.date,
        _retrieved(source.generated_at, now),
        resolution_m=resolution_m,
        # A weather-model reanalysis, as distributed (like the weather archive the agent reads), not
        # satellite data: "satellite_product" would show the farmer a "Satellite data" badge.
        kind="weather_data",
    )
    provenance["temporal"]["aggregation"] = "daily_mean"
    fact = _fact("soil", "surface_soil_moisture", value, "m3/m3", provenance)
    context["facts"].append({**fact, "depth_cm": [0, 7]})


def _add_terrain(context: dict, result: TerrainResult, field: FieldRead, now: datetime) -> None:
    low, high = result.elevation_min_m, result.elevation_max_m
    if (
        result.field_id != field.id
        or result.boundary_revision != field.boundary_revision
        or result.status != "ready"
        or result.coverage_percent < 90
        or result.source_resolution_m <= 0
        or low is None
        or high is None
        or not (isfinite(low) and isfinite(high) and low <= high)
    ):
        return
    provenance = _provenance(
        _source("Copernicus", result.source, result.source_release, result.source_url),
        f"Lowest and highest {result.source_resolution_m} m elevation cells inside the current "
        f"boundary ({result.coverage_percent:g}% covered). A static surface model that includes "
        "vegetation and buildings; it does not describe current field changes.",
        None,
        None,
        _retrieved(result.generated_at, now),
        resolution_m=result.source_resolution_m,
    )
    context["facts"].extend(
        (
            _fact("terrain", "elevation_min", low, "m", provenance),
            _fact("terrain", "elevation_max", high, "m", provenance),
        )
    )


def build_field_context(
    field: FieldRead,
    *,
    season: int,
    locale: Literal["uk", "en"],
    now: datetime,
    forecast: WaterForecast | None = None,
    vegetation: VegetationSeason | None = None,
    water: WaterResult | None = None,
    surface_moisture: GeoResult | None = None,
    terrain: TerrainResult | None = None,
    scenario: Scenario | None = None,
    revision: int | None = None,
    scouting: tuple[ScoutingEvidence, ...] = (),
    constraints: list[dict] | None = None,
) -> dict:
    """Only detached host records enter this mapping, never browser-supplied facts."""
    if now.tzinfo is None:
        raise ValueError("Agent field context requires a timezone-aware timestamp.")
    now = now.astimezone(UTC)
    ring = field.geometry.coordinates[0]
    centroid = ShapelyPolygon(ring).centroid
    context = {
        "schema_version": "0.1",
        "field_id": field.id,
        "revision": field.boundary_revision if revision is None else revision,
        "geometry": field.geometry.model_dump(mode="json"),
        "centroid": [centroid.x, centroid.y],
        "area_ha": area_hectares(ring),
        "region": {"country_iso2": "ZZ"},
        "facts": [],
        "timeseries_index": [],
        "available_layers": [],
        "available_charts": [],
        "locale": locale,
    }
    # The upstream scenario names describe other experiments. The host never
    # maps a local dry-year simulation to the specific drought_2024 scenario.
    if water is not None:
        _add_historical(context, water, season, scenario, now)
    elif forecast is not None:
        _add_forecast(context, forecast, season, now)
    if vegetation is not None:
        _add_vegetation(context, vegetation, field, season, now)
    if surface_moisture is not None:
        _add_surface_moisture(context, surface_moisture, season, now)
    if terrain is not None:
        _add_terrain(context, terrain, field, now)
    for report in scouting:
        # Structured farmer input only. Free text, GPS and images stay in the farm record.
        context["facts"].append(
            {
                "id": f"f_scouting_{report.id}",
                "domain": "farm",
                "label_key": "fact.scouting_observation",
                "category": report.category,
                "provenance": {
                    "kind": "farmer_input",
                    "data_status": "live",
                    "temporal": _range(report.date, report.date),
                    "retrieved_at": now.isoformat(),
                },
            }
        )
    if constraints:
        # The farmer's recorded decisions (confirmed or rejected), built by the host from its own
        # store for this field, season and boundary; the agent ignores rejected ones.
        context["constraints"] = constraints
    return context
