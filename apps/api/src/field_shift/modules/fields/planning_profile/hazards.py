"""Threshold observations, separate from crop damage and calibrated probabilities."""

from datetime import timedelta

from field_shift.contracts.planning_v1.field import HazardEvidence

from .evidence import reason


def dates_in(window):
    return [window.start + timedelta(days=i) for i in range((window.end - window.start).days + 1)]


VARIABLES = {
    "wind": ("wind_m_s", "m/s", max),
    "frost": ("tmin_c", "degC", min),
    "heat": ("tmax_c", "degC", max),
    "heavy_rain": ("liquid_rain_mm", "mm/day", max),
}


def threshold_evidence(kind, days, window, source, *, threshold, rule_version):
    """Report observed/modelled extreme and reviewed event threshold, never loss risk."""
    import math

    if kind not in VARIABLES or not math.isfinite(threshold):
        raise ValueError("unsupported threshold kind or nonfinite threshold")
    variable, unit, reducer = VARIABLES[kind]
    records = {d.date: d for d in days}
    expected = dates_in(window)
    usable = (
        source is not None
        and source.coverage_fraction == 1
        and source.quality == "valid"
        and all(
            d in records
            and records[d].quality in {"valid", "partial"}
            and getattr(records[d], variable) is not None
            for d in expected
        )
    )
    if not usable:
        return HazardEvidence(
            id=f"{kind}-threshold",
            kind=kind,
            status="insufficient_data",
            indicator="threshold",
            value=None,
            unit=unit,
            valid_window=window,
            source_ids=[],
            reasons=[
                reason("hazard_coverage", "Threshold coverage incomplete", version=rule_version)
            ],
        )
    extreme = reducer(getattr(records[d], variable) for d in expected)
    exceeded = extreme <= threshold if kind == "frost" else extreme >= threshold
    return HazardEvidence(
        id=f"{kind}-threshold",
        kind=kind,
        status="available",
        indicator="threshold",
        value=extreme,
        unit=unit,
        valid_window=window,
        source_ids=[source.id],
        reasons=[
            reason(
                "threshold_exceeded" if exceeded else "threshold_not_exceeded",
                f"{variable} extreme versus {threshold} {unit}; not crop loss probability",
                locator=source.product,
                version=rule_version,
                kind="source",
            )
        ],
    )
