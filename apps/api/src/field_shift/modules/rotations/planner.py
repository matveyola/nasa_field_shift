"""Bounded deterministic whole-field rotation optimization.

No weather, yield, fertilizer or profit coefficients are synthesized here. User
preferences are a transparent area-weighted objective. All agronomic intervals,
family rules, quotas and locks are explicit hard constraints.
"""

from itertools import product

from field_shift.modules.rotations.planning_catalog import CROP_FAMILIES
from field_shift.modules.rotations.planning_schemas import (
    PlannedArea,
    PlannedAssignment,
    PlannerDiagnostic,
    PlanningField,
    PlanningRequest,
    PlanningResult,
)

NODE_LIMIT = 120_000
EPSILON = 1e-7


def _allowed_sequence(
    sequence: tuple[str, ...], history: dict[int, str], request: PlanningRequest
) -> bool:
    rules = {rule.crop: rule for rule in request.crops}
    previous = dict(history)
    for offset, crop in enumerate(sequence):
        season = request.start_season + offset
        rule = rules[crop]
        if any(previous.get(year) == crop for year in range(season - rule.min_break_years, season)):
            return False
        last_crop = previous.get(season - 1)
        if request.avoid_consecutive_family and last_crop is not None:
            if CROP_FAMILIES.get(last_crop) == CROP_FAMILIES[crop]:
                return False
        previous[season] = crop
    return not request.require_two_crops or len(set(sequence)) >= 2


def optimize(
    request: PlanningRequest, fields: list[PlanningField], *, node_limit: int = NODE_LIMIT
) -> PlanningResult:
    """Return best visited feasible plan; only exhaustive search can prove optimality."""
    if node_limit < 1:
        raise ValueError("The search budget must be positive.")
    if len(fields) != len(request.field_ids) or {field.id for field in fields} != set(
        request.field_ids
    ):
        raise ValueError("The planner must receive every selected field exactly once.")
    if any(not 0 < field.area_ha <= 100_000 for field in fields):
        raise ValueError("Selected field areas must be positive and at most 100,000 hectares.")
    years = range(request.start_season, request.start_season + 3)
    crop_names = sorted(rule.crop for rule in request.crops)
    rules = {rule.crop: rule for rule in request.crops}
    total_area = sum(field.area_ha for field in fields)
    lower = {rule.crop: rule.min_area_ha for rule in request.crops}
    upper = {
        rule.crop: min(
            total_area * rule.max_share_percent / 100,
            rule.max_area_ha if rule.max_area_ha is not None else total_area,
        )
        for rule in request.crops
    }
    diagnostics: list[PlannerDiagnostic] = []
    warnings: list[PlannerDiagnostic] = []
    if sum(lower.values()) > total_area + EPSILON:
        diagnostics.append(
            PlannerDiagnostic(
                code="minimum_area_exceeds_total",
                message="The minimum crop areas exceed the selected field area.",
            )
        )
    if sum(upper.values()) < total_area - EPSILON:
        diagnostics.append(
            PlannerDiagnostic(
                code="maximum_area_below_total",
                message="The maximum crop areas cannot cover all selected fields.",
            )
        )
    for crop in crop_names:
        if lower[crop] > upper[crop] + EPSILON:
            diagnostics.append(
                PlannerDiagnostic(
                    code="crop_area_interval",
                    crop=crop,
                    message="A minimum crop area exceeds its area or share limit.",
                )
            )

    candidates: dict[str, list[tuple[float, tuple[str, ...]]]] = {}
    histories: dict[str, dict[int, str]] = {}
    locks = {(entry.field_id, entry.season): entry.crop for entry in request.locked}
    for field in fields:
        history = {entry.season: entry.crop for entry in field.history}
        history.update(
            {entry.season: entry.crop for entry in request.history if entry.field_id == field.id}
        )
        histories[field.id] = history
        lookback = max(1, max(rule.min_break_years for rule in request.crops))
        if any(
            year not in history
            for year in range(request.start_season - lookback, request.start_season)
        ):
            warnings.append(
                PlannerDiagnostic(
                    code="incomplete_history",
                    field_id=field.id,
                    message=(
                        "Crop history is incomplete. Return intervals were checked "
                        "only against known seasons."
                    ),
                )
            )
        eligible = [
            crop
            for crop in crop_names
            if field.allowed_crops is None or crop in field.allowed_crops
        ]
        choices = [
            [crop for crop in eligible if crop == locks[field.id, year]]
            if (field.id, year) in locks
            else eligible
            for year in years
        ]
        sequences = [
            (field.area_ha * sum(rules[crop].preference for crop in sequence), sequence)
            for sequence in product(*choices)
            if _allowed_sequence(sequence, history, request)
            and all(field.area_ha <= upper[crop] + EPSILON for crop in sequence)
        ]
        sequences.sort(key=lambda item: (-item[0], item[1]))
        candidates[field.id] = sequences
        if not sequences:
            diagnostics.append(
                PlannerDiagnostic(
                    code="no_field_sequence",
                    field_id=field.id,
                    message=(
                        "No crop sequence fits this field's allowed crops, history, "
                        "locks, return intervals and area limits."
                    ),
                )
            )

    def result(status: str, assignments=None, areas=None, objective=None, nodes=0):
        return PlanningResult(
            status=status,
            assignments=assignments or [],
            areas=areas or [],
            diagnostics=diagnostics,
            warnings=warnings,
            explored_nodes=nodes,
            node_limit=node_limit,
            objective=objective,
        )

    if diagnostics:
        return result("infeasible")

    # Most constrained fields first; a stable identifier provides reproducible ties.
    ordered = sorted(
        fields, key=lambda field: (len(candidates[field.id]), -field.area_ha, field.id)
    )
    remaining_max = [0.0] * (len(ordered) + 1)
    remaining_area = [0.0] * (len(ordered) + 1)
    possible = [
        {(year, crop): 0.0 for year in years for crop in crop_names}
        for _ in range(len(ordered) + 1)
    ]
    for index in range(len(ordered) - 1, -1, -1):
        field = ordered[index]
        remaining_max[index] = remaining_max[index + 1] + candidates[field.id][0][0]
        remaining_area[index] = remaining_area[index + 1] + field.area_ha
        possible[index] = dict(possible[index + 1])
        for offset, year in enumerate(years):
            available = {sequence[offset] for _, sequence in candidates[field.id]}
            for crop in available:
                possible[index][year, crop] += field.area_ha

    used = {(year, crop): 0.0 for year in years for crop in crop_names}
    selected: dict[str, tuple[str, ...]] = {}
    best: dict[str, tuple[str, ...]] | None = None
    best_score = float("-inf")
    nodes = 0
    exhausted = True

    def search(index: int, score: float) -> None:
        nonlocal best, best_score, nodes, exhausted
        if nodes >= node_limit:
            exhausted = False
            return
        nodes += 1
        if best is not None and score + remaining_max[index] <= best_score + EPSILON:
            return
        for year in years:
            if sum(max(0, lower[crop] - used[year, crop]) for crop in crop_names) > (
                remaining_area[index] + EPSILON
            ):
                return
            if any(
                used[year, crop] + possible[index][year, crop] < lower[crop] - EPSILON
                for crop in crop_names
            ):
                return
        if index == len(ordered):
            best, best_score = dict(selected), score
            return
        field = ordered[index]
        for sequence_score, sequence in candidates[field.id]:
            if nodes >= node_limit:
                exhausted = False
                break
            nodes += 1
            if any(
                used[year, crop] + field.area_ha > upper[crop] + EPSILON
                for year, crop in zip(years, sequence, strict=True)
            ):
                continue
            selected[field.id] = sequence
            for year, crop in zip(years, sequence, strict=True):
                used[year, crop] += field.area_ha
            search(index + 1, score + sequence_score)
            for year, crop in zip(years, sequence, strict=True):
                used[year, crop] -= field.area_ha
            del selected[field.id]

    search(0, 0)
    if best is None:
        diagnostics.append(
            PlannerDiagnostic(
                code="incompatible_constraints" if exhausted else "search_budget_exhausted",
                message=(
                    "No complete allocation satisfies these whole-field constraints. "
                    "Adjust quotas or locks."
                    if exhausted
                    else "The search budget ended before finding a feasible plan. "
                    "Reduce the scope or relax constraints."
                ),
            )
        )
        return result("infeasible" if exhausted else "search_limit", nodes=nodes)

    assignments: list[PlannedAssignment] = []
    allocated = {(year, crop): 0.0 for year in years for crop in crop_names}
    for field in sorted(fields, key=lambda item: item.id):
        for year, crop in zip(years, best[field.id], strict=True):
            reasons = ["Respects configured return interval", "Within farm area limits"]
            if field.allowed_crops is not None:
                reasons.append("Allowed by the field setup")
            if (field.id, year) in locks:
                reasons.append("Locked by farmer")
            if request.avoid_consecutive_family:
                reasons.append("Avoids consecutive crops in the same botanical family")
            assignments.append(
                PlannedAssignment(
                    field_id=field.id,
                    season=year,
                    crop=crop,
                    area_ha=field.area_ha,
                    locked=(field.id, year) in locks,
                    reasons=reasons,
                )
            )
            allocated[year, crop] += field.area_ha
    areas = [
        PlannedArea(
            season=year,
            crop=crop,
            area_ha=round(allocated[year, crop], 6),
            share_percent=round(100 * allocated[year, crop] / total_area, 6),
            min_area_ha=lower[crop],
            max_area_ha=upper[crop],
        )
        for year in years
        for crop in crop_names
    ]
    return result(
        "optimal" if exhausted else "feasible", assignments, areas, round(best_score, 6), nodes
    )
