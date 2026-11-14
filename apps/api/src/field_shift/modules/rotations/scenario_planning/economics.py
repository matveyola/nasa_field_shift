"""Scenario gross margin; irrigation is an explicit additional variable cost.

The v1 cost convention is variable_cost_per_ha EXCLUDES irrigation. A zero
irrigation cost must be supplied explicitly for already prepaid/free irrigation.
Intervals are scenario ranges, never calibrated probability intervals.
"""

from field_shift_contracts.planning_v1.common import Metric, Uncertainty
from field_shift_contracts.planning_v1.planning import ScenarioEconomics

from .common import reason


def metric(value=None, lower=None, upper=None, missing=None):
    return Metric(
        status="insufficient_data" if missing else "available",
        value=value,
        lower=lower,
        upper=upper,
        unit="currency",
        reasons=missing or [],
        uncertainty=Uncertainty(
            kind="scenario_range" if lower is not None else "none",
            coverage=None,
            calibration_scope=None,
            method_version="scenario-economics-v1",
        ),
    )


def aggregate(request, selected, evaluations):
    """Return all season/member totals, preserving cross-field trajectory identity."""
    result = []
    fields = {f.field_id: f for f in request.fields}
    assumptions = {
        (e.crop.model_dump_json(), e.season, e.trajectory_id): e for e in request.economics
    }
    for scenario in request.weather.scenarios:
        for season in range(request.start_season, request.start_season + request.season_count):
            points, lows, highs, costs, waters, ids, missing = [], [], [], [], [], [], []
            currencies = set()
            for candidate in selected:
                area = fields[candidate.field_id].area_ha
                assignment = next(a for a in candidate.assignments if a.season == season)
                evaluation = evaluations[candidate.sequence_id]
                row = next(
                    r
                    for r in evaluation.seasons
                    if r.season == season and r.trajectory_id == scenario.trajectory_id
                )
                e = assumptions.get(
                    (assignment.crop.model_dump_json(), season, scenario.trajectory_id)
                )
                water = (
                    (
                        row.irrigation_mm.upper
                        if row.irrigation_mm.upper is not None
                        else row.irrigation_mm.value
                    )
                    if row.irrigation_mm.status == "available"
                    else None
                )
                waters.append(None if water is None else water * area * 10)
                if e:
                    ids.append(e.assumption_id)
                    if e.currency:
                        currencies.add(e.currency)
                cost = None
                if e and e.variable_cost_per_ha is not None and water is not None:
                    if water == 0:
                        cost = e.variable_cost_per_ha
                    elif e.irrigation_cost_per_mm_ha is not None:
                        cost = e.variable_cost_per_ha + water * e.irrigation_cost_per_mm_ha
                costs.append(None if cost is None else cost * area)
                y = row.yield_t_ha
                point, low, high = y.value, y.lower, y.upper
                valid_yield = evaluation.applicability == "validated" and y.status == "available"
                basis = row.harvest_basis
                if e and e.yield_origin == "user_assumption":
                    point = low = high = e.user_yield_t_ha
                    basis = e.harvest_basis
                    valid_yield = True
                if (
                    not e
                    or not valid_yield
                    or e.price_per_t is None
                    or cost is None
                    or e.currency is None
                    or basis != e.harvest_basis
                ):
                    missing.append(
                        reason(
                            "economics_unavailable",
                            "Missing price, cost, water, harvest basis or validated yield; "
                            "no default return is assumed.",
                            e.assumption_id if e else candidate.sequence_id,
                            "assumption" if e else "result",
                        )
                    )
                    continue
                points.append(None if point is None else (point * e.price_per_t - cost) * area)
                lows.append(((low if low is not None else point) * e.price_per_t - cost) * area)
                highs.append(((high if high is not None else point) * e.price_per_t - cost) * area)
            result.append(
                ScenarioEconomics(
                    trajectory_id=scenario.trajectory_id,
                    season=season,
                    currency=next(iter(currencies)) if len(currencies) == 1 else None,
                    gross_margin=metric(missing=missing[:50])
                    if missing
                    else metric(
                        value=None if None in points else sum(points),
                        lower=sum(lows),
                        upper=sum(highs),
                    ),
                    irrigation_m3=None if None in waters else sum(waters),
                    variable_cost=None if None in costs else sum(costs),
                    assumption_ids=sorted(set(ids)),
                )
            )
    return result
