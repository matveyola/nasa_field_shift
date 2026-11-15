"""Historical challenger with explicit provenance, chronology and execution scope."""

import math
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from .artifacts import FEATURES, load_artifacts
from .features import RejectedSnapshot, prepare
from .types import HistoricalRequest, SurfaceResult

MAX_DAYS = 10000


def _utc(value):
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise RejectedSnapshot("insufficient_data", "Explicit UTC availability timestamps required")
    return value


def _number(value, low, high, *, optional=False):
    if value is None and optional:
        return
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise RejectedSnapshot("insufficient_data", "Nonfinite or physically invalid input")


def _source(record, as_of):
    if not isinstance(record.source_sha256, str) or not re.fullmatch(
        "[0-9a-f]{64}", record.source_sha256
    ):
        raise RejectedSnapshot("insufficient_data", "SHA256 source identity required")
    if _utc(record.available_at) > as_of:
        raise RejectedSnapshot("insufficient_data", "Input unavailable at requested as_of")


def _validate(request, receipt):
    as_of = _utc(request.as_of)
    if type(request.estimated_on) is not date:
        raise RejectedSnapshot("insufficient_data", "Explicit estimate date required")
    if as_of < datetime.combine(request.estimated_on + timedelta(days=1), datetime.min.time(), UTC):
        raise RejectedSnapshot("unsupported", "Daily historical reconstruction cannot be a nowcast")
    if as_of < datetime.fromisoformat(receipt["source_available_at"]):
        raise RejectedSnapshot(
            "unsupported", "Pinned final artifact unavailable at requested as_of"
        )
    _number(request.latitude, -90, 90)
    _number(request.longitude, -180, 180)
    scope = receipt["execution_scope"]
    west, south, east, north = scope["bbox_west_south_east_north"]
    if request.region_code != scope["region_code"] or not (
        west <= request.longitude <= east and south <= request.latitude <= north
    ):
        raise RejectedSnapshot("out_of_domain", "Outside experimental Iowa execution footprint")
    if request.estimated_on.month not in scope["months"]:
        raise RejectedSnapshot("unsupported", "Only April–October research season supported")
    if len(request.era5) > MAX_DAYS or len(request.smap) > MAX_DAYS:
        raise RejectedSnapshot("unsupported", "Snapshot exceeds 10000 days per product")
    for records in (request.era5, request.smap):
        seen = set()
        for record in records:
            if type(record.day) is not date or record.day in seen:
                raise RejectedSnapshot("insufficient_data", "Unique dated product records required")
            seen.add(record.day)
            if record.day > request.estimated_on:
                raise RejectedSnapshot(
                    "insufficient_data", "Future observations are not predictors"
                )
            _source(record, as_of)
            if record.available_at.date() < record.day:
                raise RejectedSnapshot(
                    "insufficient_data", "Product availability predates observation"
                )
            _number(record.surface_vwc, 0, 1, optional=True)
    for r in request.era5:
        _number(r.subsurface_vwc, 0, 1, optional=True)
        _number(r.soil_temperature_c, -100, 80, optional=True)
        _number(r.air_temperature_c, -100, 80, optional=True)
        _number(r.snow_depth_m, 0, 100, optional=True)
        if r.available_at < datetime.combine(r.day + timedelta(days=1), datetime.min.time(), UTC):
            raise RejectedSnapshot(
                "insufficient_data", "Daily ERA5 availability predates complete day"
            )
    if request.soil is not None:
        soil = request.soil
        _source(soil, as_of)
        if soil.source != "USDA_SSURGO" or soil.sample_depth_cm != 5:
            raise RejectedSnapshot("unsupported", "Original SSURGO properties at 5 cm required")
        for value in (soil.sand_percent, soil.clay_percent, soil.organic_matter_percent):
            _number(value, 0, 100)
        if soil.sand_percent + soil.clay_percent > 100:
            raise RejectedSnapshot("insufficient_data", "Soil fractions exceed 100 percent")


class HistoricalSurfaceAdapter:
    """Read-only model; no fetching, fitting, registry mutation or automatic promotion.

    The bounding rectangle is deliberately only an execution restriction. It does
    not establish state membership, measured field support or geographic accuracy.
    """

    def __init__(self, artifact_directory: Path | None = None):
        self._receipt, self._meta, self._text = load_artifacts(artifact_directory)
        self._models = None

    def estimate(self, request: HistoricalRequest) -> SurfaceResult:
        try:
            _validate(request, self._receipt)
            features, level, amplitude = prepare(request)
        except RejectedSnapshot as exc:
            return SurfaceResult(exc.status, exc.reason, None, request.estimated_on, None)
        records = [*request.era5, *request.smap]
        if request.soil is not None:
            records.append(request.soil)
        # Conservative snapshot availability includes every supplied record and model receipt.
        available = max(
            datetime.fromisoformat(self._receipt["source_available_at"]),
            *(r.available_at for r in records),
        )
        sources = tuple(sorted({r.source_sha256 for r in records}))
        if features is None:
            return SurfaceResult(
                "available",
                "No recommended L3 at age 2–5 days; raw ERA5-Land reference",
                "raw_era5_fallback",
                request.estimated_on,
                available,
                value_m3_m3=level,
                source_sha256=sources,
                target_depth_cm=(0, 7),
            )
        prediction, low, high = self._predict(features, level, amplitude)
        if not all(math.isfinite(v) for v in (prediction, low, high)):
            raise ValueError("Pinned research model produced nonfinite output")
        return SurfaceResult(
            "available",
            "Experimental delayed reconstruction; not a forecast or validated field map",
            "experimental_l3_correction",
            request.estimated_on,
            available,
            value_m3_m3=prediction,
            research_interval_m3_m3=(low, high),
            source_sha256=sources,
        )

    def _predict(self, features, level, amplitude):
        if self._models is None:
            try:
                import lightgbm
            except ImportError as exc:
                raise ImportError(
                    "Install field-shift-models[ruslan] for historical L3 inference"
                ) from exc
            if lightgbm.__version__ != "4.6.0":
                raise RuntimeError("Historical research inference requires pinned LightGBM 4.6.0")
            self._models = {
                name: lightgbm.Booster(model_str=text, params={"num_threads": 1})
                for name, text in self._text.items()
            }
            if any(tuple(m.feature_name()) != FEATURES for m in self._models.values()):
                raise ValueError("Loaded model feature order mismatch")
        predicted = {
            name: level + amplitude * float(model.predict([features], num_threads=1)[0])
            for name, model in self._models.items()
        }
        cqr = self._meta["cqr_half_width_m3m3"]
        return (
            min(0.6, max(0.01, predicted["gbm"])),
            min(predicted["q10"], predicted["q90"]) - cqr,
            max(predicted["q10"], predicted["q90"]) + cqr,
        )
