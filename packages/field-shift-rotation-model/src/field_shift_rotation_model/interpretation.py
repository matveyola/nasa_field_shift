"""Interpretation gates around original calculations; no latitude suitability rule."""

from contextlib import contextmanager
from inspect import signature
from math import fsum

from .modules.rotations import hybrid_forecast


def diagnostic(code, message, *, crop=None, details=None, severity="blocking"):
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "crop_id": crop.crop_id if crop else None,
        "start_date": crop.first_day.isoformat() if crop else None,
        "end_date": crop.last_day.isoformat() if crop else None,
        "details": details or {},
    }


def thermal_diagnostics(crop, weather, demand):
    diagnostics = []
    positive_mean = fsum(max(0.0, (w.high_c + w.low_c) / 2) for w in weather)
    details = {
        "weather_days": len(weather),
        "positive_mean_degree_days_above_zero": positive_mean,
        "modeled_demand_mm": demand,
    }
    if positive_mean <= 0 or demand <= 1e-9:
        diagnostics.append(
            diagnostic(
                "no_thermal_growth",
                (
                    "A zero-demand or entirely nonpositive-temperature crop cycle cannot be "
                    "ranked as a viable sowing option."
                ),
                crop=crop,
                details=details,
            )
        )
    elif crop.profile.base_c is not None and crop.profile.flowering_gdd:
        heat = fsum(
            hybrid_forecast.growing_degree_days(
                w.low_c, w.high_c, crop.profile.base_c, crop.profile.upper_c
            )
            for w in weather
        )
        if heat < crop.profile.flowering_gdd[0]:
            diagnostics.append(
                diagnostic(
                    "reference_flowering_heat_unmet",
                    (
                        "The supplied weather does not reach the profile's reference "
                        "flowering heat; "
                        "review crop and local calendar before using its rank."
                    ),
                    crop=crop,
                    details=details
                    | {
                        "degree_days": heat,
                        "reference_flowering_lower_degree_days": crop.profile.flowering_gdd[0],
                    },
                )
            )
    return diagnostics


@contextmanager
def collect_forecast_diagnostics():
    # Production runs each call in its own bounded process. Restore even on exceptions.
    original = hybrid_forecast.run_crop
    diagnostics = []

    def checked(*args, **kwargs):
        bound = signature(original).bind(*args, **kwargs).arguments
        weather, _ = hybrid_forecast.weather_for_crop(
            bound["crop"], bound["live"], bound["archive"], bound["anchor"], bound["first"]
        )
        result = original(*args, **kwargs)
        diagnostics.extend(thermal_diagnostics(bound["crop"], weather, result[0].demand_mm))
        return result

    hybrid_forecast.run_crop = checked
    try:
        yield diagnostics
    finally:
        hybrid_forecast.run_crop = original


def interpretation(land_status, diagnostics):
    rows = list(diagnostics)
    if land_status != "arable":
        rows.append(
            diagnostic(
                "land_suitability_not_assessed",
                (
                    "Current trusted arable-land evidence is missing; scenario calculations "
                    "do not establish field suitability."
                ),
            )
        )
    status = "arable_land_only" if land_status == "arable" else "not_assessed"
    if any(row["code"] == "no_thermal_growth" for row in rows):
        status = "no_thermal_growth"
    elif diagnostics:
        status = "review_required"
    return {
        "ranking_usable": land_status == "arable" and not diagnostics,
        "suitability_status": status,
        "diagnostics": rows,
    }


def climate_diagnostics(payload):
    """Preserve original climate ranks but flag frost/incomplete or zero-demand cycles."""
    rows = []

    def visit(value):
        if isinstance(value, dict):
            if "end_reason" in value and "metrics" in value:
                reason = value["end_reason"]
                demand = value["metrics"].get("demand_mm")
                if reason not in {"maturity", "harvest"} or demand is None or demand <= 1e-9:
                    row = diagnostic(
                        "climate_cycle_requires_review",
                        (
                            "A modeled crop cycle lacks maturity or positive demand; "
                            "its rank is not a sowing recommendation."
                        ),
                        details={"end_reason": reason, "demand_mm": demand},
                    )
                    row.update(
                        crop_id=value.get("crop_id"),
                        start_date=value.get("planting_date"),
                        end_date=value.get("end_date"),
                    )
                    if row not in rows:
                        rows.append(row)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(payload)
    return rows
