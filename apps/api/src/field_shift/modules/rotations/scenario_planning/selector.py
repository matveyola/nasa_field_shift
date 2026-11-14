"""Bounded deterministic farm-wide enumeration; hard constraints never enter a penalty.

Optimality is only over the complete supplied, eligible candidate universe. Unknown
suitability or resource data prevents a proof of infeasibility or optimality.
"""

from time import monotonic

from field_shift_contracts.planning_v1.planning import (
    FarmPlanComparison,
    PlanAlternative,
    SnapshotRef,
)

from .common import digest, reason
from .economics import aggregate


class Cancelled(Exception):
    pass


def upper(metric):
    if metric.status != "available":
        return None
    return metric.upper if metric.upper is not None else metric.value


def candidate_errors(request, candidate, evaluation):
    fields = {f.field_id: f for f in request.fields}
    field = fields.get(candidate.field_id)
    seasons = list(range(request.start_season, request.start_season + request.season_count))
    errors = []
    if (
        field is None
        or candidate.field_snapshot_id != field.snapshot_id
        or candidate.rulebook_version != request.rulebook_version
        or [a.season for a in candidate.assignments] != seasons
    ):
        return [
            reason(
                "candidate_scope",
                "Candidate does not match immutable input scope.",
                candidate.sequence_id,
                "result",
            )
        ]
    for assessment in candidate.assessments:
        if assessment.status != "eligible" or assessment.unchecked_rule_ids:
            errors.extend(assessment.reasons)
            if not assessment.reasons:
                errors.append(
                    reason(
                        "unchecked_rules",
                        "Required rules are unchecked.",
                        assessment.assessment_id,
                        "rule",
                    )
                )
    for a in candidate.assignments:
        if a.crop not in request.enabled_crops:
            errors.append(
                reason(
                    "disabled_crop", "Crop profile is not enabled.", candidate.sequence_id, "result"
                )
            )
        lock = next(
            (x for x in request.locks if x.field_id == a.field_id and x.season == a.season), None
        )
        if lock and a != lock:
            errors.append(
                reason(
                    "lock_conflict", "Candidate changes a confirmed commitment.", lock.commitment_id
                )
            )
    if errors and all(a.status in ("eligible", "ineligible") for a in candidate.assessments):
        return errors
    if evaluation is None:
        return errors + [
            reason(
                "missing_evaluation",
                "No model evaluation for sequence.",
                candidate.sequence_id,
                "result",
            )
        ]
    expected = {
        (a.season, s.trajectory_id)
        for a in candidate.assignments
        for s in request.weather.scenarios
    }
    actual = {(r.season, r.trajectory_id) for r in evaluation.seasons}
    if (
        evaluation.sequence_id != candidate.sequence_id
        or evaluation.field_snapshot_id != field.snapshot_id
        or evaluation.scenario_set_id != request.weather.scenario_set_id
        or evaluation.as_of != request.weather.as_of
        or evaluation.as_of > request.as_of
        or actual != expected
        or any(
            r.crop != next(a.crop for a in candidate.assignments if a.season == r.season)
            for r in evaluation.seasons
            if r.season in seasons
        )
    ):
        errors.append(
            reason(
                "evaluation_scope",
                "Evaluation does not match input seasons/members.",
                evaluation.evaluation_id,
                "result",
            )
        )
        return errors
    if evaluation.applicability in ("unsupported", "out_of_domain"):
        errors.extend(
            evaluation.reasons
            or [
                reason(
                    "model_scope", "Model is outside its scope.", evaluation.evaluation_id, "model"
                )
            ]
        )
    for row in evaluation.seasons:
        if row.status != "available":
            errors.extend(row.reasons)
            errors.append(
                reason(
                    "unavailable_output",
                    "Crop evaluation is unavailable.",
                    evaluation.evaluation_id,
                    "result",
                )
            )
        irrigation = next((i for i in field.management.irrigation if i.season == row.season), None)
        water = upper(row.irrigation_mm)
        if irrigation is None or irrigation.max_mm is None or water is None:
            errors.append(
                reason(
                    "unknown_irrigation",
                    "Cannot verify field irrigation limit.",
                    field.snapshot_id,
                    "source",
                )
            )
        elif water > irrigation.max_mm:
            errors.append(
                reason(
                    "field_irrigation",
                    "Irrigation exceeds field availability.",
                    field.snapshot_id,
                    "source",
                )
            )
    return errors


def constraints(request, selected, evaluations, results):
    errors = []
    fields = {f.field_id: f for f in request.fields}
    assignments = [a for c in selected for a in c.assignments]
    total_area = sum(f.area_ha for f in request.fields)
    for limit in request.crop_areas:
        area = sum(
            fields[a.field_id].area_ha
            for a in assignments
            if a.season == limit.season and a.crop.crop_id == limit.crop_id
        )
        if (
            area < limit.min_ha
            or (limit.max_ha is not None and area > limit.max_ha)
            or (limit.max_share is not None and area > total_area * limit.max_share)
        ):
            errors.append(
                reason("crop_area", "Crop area/share violates a hard limit.", limit.constraint_id)
            )
    for limit in request.resources:
        for scenario in request.weather.scenarios:
            result = next(
                r
                for r in results
                if r.season == limit.season and r.trajectory_id == scenario.trajectory_id
            )
            if limit.budget is not None and (
                result.variable_cost is None
                or result.currency != limit.currency
                or result.variable_cost > limit.budget
            ):
                errors.append(
                    reason(
                        "budget",
                        "Scenario costs exceed budget or are unknown.",
                        limit.constraint_id,
                    )
                )
            if limit.irrigation_m3 is not None:
                water = [
                    upper(r.irrigation_mm) * fields[c.field_id].area_ha * 10
                    for c in selected
                    for r in evaluations[c.sequence_id].seasons
                    if r.season == limit.season and r.trajectory_id == scenario.trajectory_id
                ]
                if sum(water) > limit.irrigation_m3:
                    errors.append(
                        reason(
                            "farm_irrigation",
                            "Scenario irrigation exceeds farm water.",
                            limit.constraint_id,
                        )
                    )
    # Conservative reservation: all hectares of each overlapping sow/harvest
    # operation consume capacity. No undocumented flexible scheduling is assumed.
    for limit in request.equipment:
        area = 0
        unknown = False
        for a in assignments:
            field = fields[a.field_id]
            if limit.equipment_id not in field.management.equipment_ids:
                continue
            for window in (a.sowing_window, a.harvest_window):
                if window is None:
                    unknown = True
                elif window.start <= limit.window.end and window.end >= limit.window.start:
                    area += field.area_ha
        if unknown or area > limit.capacity_ha:
            errors.append(
                reason(
                    "equipment_unknown" if unknown else "equipment",
                    "Calendar capacity exceeded or operation window unknown.",
                    limit.constraint_id,
                )
            )
    return errors


def objective(request, results, selected, evaluations):
    if request.goal == "water_demand":
        fields = {f.field_id: f for f in request.fields}
        return max(
            sum(
                upper(r.irrigation_mm) * fields[c.field_id].area_ha
                for c in selected
                for r in evaluations[c.sequence_id].seasons
                if r.trajectory_id == s.trajectory_id
            )
            for s in request.weather.scenarios
        )
    totals = {}
    for r in results:
        if r.gross_margin.status != "available" or r.gross_margin.value is None:
            return None
        totals[r.trajectory_id] = totals.get(r.trajectory_id, 0) + r.gross_margin.value
    if request.goal == "reference_margin":
        return totals[request.reference_trajectory_id]
    return min(totals.values())


class BoundedSelector:
    def __init__(self, *, clock=monotonic, cancelled=lambda: False):
        self.clock = clock
        self.cancelled = cancelled

    def compare(self, request, candidates, evaluations):
        started = self.clock()
        limits = request.solver_limits
        # Local measured-payload admission ceiling; never silently truncate input.
        if len(candidates) > 3000 or len(evaluations) > 3000:
            raise ValueError("at most 3000 candidates/evaluations admitted per run")
        if len({c.sequence_id for c in candidates}) != len(candidates):
            raise ValueError("duplicate sequence IDs")
        by_id = {e.sequence_id: e for e in evaluations}
        if len(by_id) != len(evaluations):
            raise ValueError("duplicate sequence evaluations")
        groups = {f.field_id: [] for f in sorted(request.fields, key=lambda f: f.field_id)}
        diagnostics = {}
        unknown = False
        for c in sorted(candidates, key=lambda c: c.sequence_id):
            errs = candidate_errors(request, c, by_id.get(c.sequence_id))
            for e in errs:
                diagnostics[(e.code, e.evidence[0].id)] = e
            if errs:
                unknown |= by_id.get(c.sequence_id) is not None and by_id[
                    c.sequence_id
                ].applicability in ("unsupported", "out_of_domain")
                unknown |= any(
                    a.status not in ("eligible", "ineligible") or a.unchecked_rule_ids
                    for a in c.assessments
                ) or any(
                    e.code
                    in (
                        "missing_evaluation",
                        "unknown_irrigation",
                        "evaluation_scope",
                        "candidate_scope",
                        "unavailable_output",
                        "model_scope",
                    )
                    for e in errs
                )
            else:
                groups[c.field_id].append(c)
        if any(len(g) > limits.max_candidates_per_field for g in groups.values()):
            raise ValueError("candidate count exceeds declared per-field limit")
        retained = []
        seen = set()
        nodes = 0
        limited = False

        def visit(depth, chosen):
            nonlocal nodes, limited, unknown
            if self.cancelled():
                raise Cancelled()
            if nodes >= limits.max_nodes or self.clock() - started >= limits.max_seconds:
                limited = True
                return
            nodes += 1
            if depth < len(groups):
                for c in list(groups.values())[depth]:
                    visit(depth + 1, [*chosen, c])
                    if limited:
                        break
                return
            results = aggregate(request, chosen, by_id)
            errs = constraints(request, chosen, by_id, results)
            for e in errs:
                diagnostics[(e.code, e.evidence[0].id)] = e
            if errs:
                unknown |= any(e.code == "equipment_unknown" for e in errs)
                unknown |= any(r.variable_cost is None for r in results) and any(
                    e.code == "budget" for e in errs
                )
                return
            value = objective(request, results, chosen, by_id)
            if value is None:
                unknown = True
                e = reason(
                    "objective_unavailable",
                    "Objective lacks complete scenario economics.",
                    request.request_id,
                    "result",
                )
                diagnostics[(e.code, e.evidence[0].id)] = e
                return
            assignments = [a for c in chosen for a in c.assignments]
            key = tuple(
                sorted((a.field_id, a.season, a.crop.model_dump_json()) for a in assignments)
            )
            if key in seen:
                return
            p = PlanAlternative(
                alternative_id="plan-" + digest(key)[:24],
                label=f"{request.goal} scenario plan",
                assignments=assignments,
                sequence_ids=[c.sequence_id for c in chosen],
                evaluation_ids=[by_id[c.sequence_id].evaluation_id for c in chosen],
                scenario_results=results,
                objective_value=value,
                objective_unit="mm_ha" if request.goal == "water_demand" else "currency",
                reasons=[
                    reason(
                        "policy",
                        "Ranked over coherent tested trajectories; no probability "
                        "or net profit is asserted.",
                        request.request_id,
                        "result",
                    )
                ],
            )
            retained.append(p)
            retained.sort(
                key=lambda p: (
                    (p.objective_value if request.goal == "water_demand" else -p.objective_value),
                    p.alternative_id,
                )
            )
            del retained[limits.max_alternatives :]
            seen.clear()
            seen.update(
                tuple(
                    sorted((a.field_id, a.season, a.crop.model_dump_json()) for a in p.assignments)
                )
                for p in retained
            )

        visit(0, [])
        if any(not g for g in groups.values()):
            e = reason(
                "no_candidates",
                "At least one field has no admissible candidate sequence.",
                request.rulebook_version,
                "rule",
            )
            diagnostics[(e.code, e.evidence[0].id)] = e
            # Absent provider output cannot prove infeasibility.
            unknown |= any(not any(c.field_id == f for c in candidates) for f in groups)
        if limited:
            e = reason(
                "search_limit",
                "Search bound reached; no proof of optimality/infeasibility.",
                request.request_id,
                "result",
            )
            diagnostics[(e.code, e.evidence[0].id)] = e
        status = (
            "limit_reached"
            if limited
            else "feasible"
            if retained and unknown
            else "optimal"
            if retained
            else "not_run"
            if unknown
            else "infeasible"
        )
        return FarmPlanComparison(
            comparison_id="comparison-" + digest(request)[:24],
            request_id=request.request_id,
            farm_id=request.farm_id,
            solver_status=status,
            explored_nodes=nodes,
            elapsed_seconds=max(0, self.clock() - started),
            alternatives=retained,
            snapshots=[
                SnapshotRef(
                    field_id=f.field_id,
                    snapshot_id=f.snapshot_id,
                    profile_revision=f.profile_revision,
                    geometry_revision=f.geometry_revision,
                )
                for f in request.fields
            ],
            scenario_set_id=request.weather.scenario_set_id,
            rulebook_version=request.rulebook_version,
            input_sha256=digest(request),
            diagnostics=list(diagnostics.values())[:100],
        )
