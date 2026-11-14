"""Narrow A's permissible windows using separately sourced exact operation dates."""

from datetime import date

from field_shift.contracts.planning_v1.common import (
    Contract,
    CropRef,
    DateWindow,
    Id,
    Season,
    Text,
    Utc,
)
from field_shift.modules.rotations.scenario_planning.common import reason
from field_shift.modules.rotations.suitability.engine import SuitabilityEngine


class Calendar(Contract):
    field_id: Id
    crop: CropRef
    season: Season
    sowing_date: date
    harvest_date: date
    source: Text
    available_at: Utc


class CalendarSuitability(SuitabilityEngine):
    def __init__(self, bundle):
        super().__init__(
            bundle.rulebook,
            region_for=lambda f: bundle.regions[f.field_id],
            allow_synthetic=bundle.synthetic,
        )
        self.calendars = bundle.calendars

    def candidates(self, request):
        candidates = super().candidates(request)
        for c in candidates:
            for a, assessment in zip(c.assignments, c.assessments):
                exact = next(
                    (
                        x
                        for x in self.calendars
                        if x.field_id == a.field_id and x.crop == a.crop and x.season == a.season
                    ),
                    None,
                )
                if exact is None or a.locked or exact.available_at > request.as_of:
                    continue
                if (
                    a.sowing_window is None
                    or a.harvest_window is None
                    or not a.sowing_window.start <= exact.sowing_date <= a.sowing_window.end
                    or not a.harvest_window.start <= exact.harvest_date <= a.harvest_window.end
                    or exact.sowing_date >= exact.harvest_date
                ):
                    assessment.status = "ineligible"
                    assessment.reasons.append(
                        reason(
                            "calendar_outside_rules",
                            "Chosen operation dates violate the reviewed windows.",
                            exact.source,
                            "assumption",
                        )
                    )
                    continue
                a.sowing_window = DateWindow(start=exact.sowing_date, end=exact.sowing_date)
                a.harvest_window = DateWindow(start=exact.harvest_date, end=exact.harvest_date)
                assessment.reasons.append(
                    reason(
                        "exact_calendar",
                        "Explicit operation dates within the reviewed windows.",
                        exact.source,
                        "assumption",
                    )
                )
        return candidates


def candidate_coverage(request):
    """A generates a bounded prefix, which cannot prove full-farm infeasibility."""
    result = {}
    for profile in request.fields:
        locked = {a.season for a in request.locks if a.field_id == profile.field_id}
        free_seasons = sum(
            y not in locked
            for y in range(request.start_season, request.start_season + request.season_count)
        )
        total = len(request.enabled_crops) ** free_seasons
        result[profile.field_id] = {
            "combinations": total,
            "limit": request.solver_limits.max_candidates_per_field,
            "complete": total <= request.solver_limits.max_candidates_per_field,
        }
    return result


def mark_search_coverage(comparison, request):
    limited = [key for key, row in candidate_coverage(request).items() if not row["complete"]]
    if not limited:
        return comparison
    diagnostic = reason(
        "candidate_generation_limit",
        "Candidate generation reached its per-field limit. An unsearched plan may be better "
        "or feasible; increase the limit or reduce scope before drawing a final conclusion.",
        "solver_limits.max_candidates_per_field",
        "assumption",
    )
    return comparison.model_copy(
        update={
            "solver_status": "limit_reached",
            "diagnostics": [diagnostic, *comparison.diagnostics][:100],
        }
    )
