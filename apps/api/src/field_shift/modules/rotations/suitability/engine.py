"""Deterministic bounded eligibility and candidate generation via frozen ports."""

import hashlib
import itertools

from field_shift_contracts.planning_v1 import CandidateSequence, SuitabilityAssessment
from field_shift_contracts.planning_v1.field import CropAssignment
from field_shift_science.fields.land import qualified_land_sources

from .evidence import reason
from .rules import dates_in


def identity(*parts):
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:32]


class SuitabilityEngine:
    def __init__(self, rulebook, *, region_for, allow_synthetic=False):
        self.rulebook = rulebook
        self.region_for = region_for
        self.allow_synthetic = allow_synthetic

    def evaluate(self, profile, crop, season, weather, *, history=None, as_of=None):
        rules = [r for r in self.rulebook.rules if r.crop == crop and r.applies(season)]
        region = self.region_for(profile)
        rule = next((r for r in rules if r.region == region), None)
        reasons, checked, unchecked = [], [], []
        statuses = []

        def add(status, code, message, locator=None):
            statuses.append(status)
            reasons.append(
                reason(
                    code,
                    message,
                    locator or (rule.source if rule else "rulebook"),
                    self.rulebook.version,
                )
            )

        land_sources = qualified_land_sources(
            profile, as_of=as_of or profile.as_of, allow_synthetic=self.allow_synthetic
        )
        if profile.land_status in {"ice", "ocean", "non_arable"} and land_sources:
            checked.append("land")
            add(
                "ineligible",
                "non_arable",
                "Proven non-arable outdoor polygon",
                land_sources[0].product,
            )
        elif profile.land_status != "arable" or not land_sources:
            unchecked.append("land")
            add("insufficient_data", "land_unknown", "Missing complete valid land coverage")
        else:
            checked.append("land")
        if not rule:
            add(
                "out_of_domain" if rules else "unsupported",
                "rule_missing",
                "No applicable crop/cultivar/season/region rule",
            )
        elif rule.review_status == "unreviewed" or (
            rule.review_status == "synthetic_test" and not self.allow_synthetic
        ):
            unchecked.append(rule.id)
            add("insufficient_data", "unreviewed_rule", "Rule has no production review")
        else:
            checked.append(rule.id)
            if (
                weather is None
                or weather.stale_after <= (as_of or profile.as_of)
                or weather.as_of > (as_of or profile.as_of)
            ):
                add("insufficient_data", "weather_missing", "No usable issue-time weather")
            else:
                required_dates = set(dates_in(rule.growth_window))
                sow_dates = set(dates_in(rule.sowing_window))
                for scenario in weather.scenarios:
                    trace = next(
                        (t for t in scenario.traces if t.field_snapshot_id == profile.snapshot_id),
                        None,
                    )
                    if trace is None:
                        add("insufficient_data", "trace_missing", "No field weather trace")
                        continue
                    source = next(s for s in weather.sources if s.id == trace.source_id)
                    if source.quality != "valid" or source.coverage_fraction != 1:
                        add(
                            "insufficient_data",
                            "weather_coverage",
                            "Weather coverage/QC incomplete",
                        )
                        continue
                    days = {d.date: d for d in trace.days if d.quality in {"valid", "partial"}}
                    if not required_dates <= days.keys() or any(
                        days[d].tmin_c is None or days[d].tmax_c is None for d in required_dates
                    ):
                        add(
                            "insufficient_data", "thermal_missing", "Incomplete thermal-time window"
                        )
                    else:
                        thermal = sum(
                            max(0, (days[d].tmin_c + days[d].tmax_c) / 2 - rule.base_temperature_c)
                            for d in required_dates
                        )
                        if thermal < rule.required_degree_days:
                            add(
                                "ineligible",
                                "thermal_time",
                                "Insufficient degree days in crop window",
                            )
                    irrigation = next(
                        (i for i in profile.management.irrigation if i.season == season), None
                    )
                    if rule.min_seasonal_supply_mm > 0:
                        if (
                            not required_dates <= days.keys()
                            or any(days[d].liquid_rain_mm is None for d in required_dates)
                            or irrigation is None
                            or irrigation.max_mm is None
                        ):
                            add(
                                "insufficient_data",
                                "water_missing",
                                "Missing seasonal rain or explicit irrigation allocation",
                            )
                        else:
                            supply = sum(days[d].liquid_rain_mm for d in required_dates)
                            supply += irrigation.max_mm
                            if supply < rule.min_seasonal_supply_mm:
                                add(
                                    "ineligible",
                                    "water_supply",
                                    "Rain plus maximum irrigation below reviewed necessary supply",
                                )
                    if rule.purpose == "new_sowing" and rule.require_unfrozen_sowing:
                        if not sow_dates <= days.keys() or any(
                            days[d].soil_frozen is None for d in sow_dates
                        ):
                            add("insufficient_data", "frozen_missing", "Missing sowing soil state")
                        elif all(days[d].soil_frozen for d in sow_dates):
                            add("ineligible", "frozen_sowing", "No unfrozen day in sowing window")
            horizons = sorted(profile.soil, key=lambda h: h.top_cm)
            depth = 0
            for horizon in horizons:
                if horizon.top_cm != depth or horizon.quality != "valid" or horizon.ph is None:
                    break
                if horizon.ph < rule.min_ph or horizon.ph > rule.max_ph:
                    add("ineligible", "soil_ph", "Soil pH outside reviewed rule")
                depth = horizon.bottom_cm
                if depth >= rule.min_soil_depth_cm:
                    break
            if depth < rule.min_soil_depth_cm:
                add("insufficient_data", "soil_depth", "Missing valid continuous soil depth/pH")
            irrigation = next(
                (i for i in profile.management.irrigation if i.season == season), None
            )
            if irrigation is None or irrigation.system == "unknown":
                add("insufficient_data", "management_missing", "Unknown irrigation management")
            elif irrigation.system not in rule.irrigation_systems:
                add("ineligible", "management", "Irrigation system outside crop rule")
            by_year = {h.season: h for h in profile.history}
            # Planned earlier assignments are known choices, not invented historical records.
            planned = {a.season: a.crop for a in (history or [])}
            lookback = max(rule.min_return_years, rule.family_return_years) - 1
            families = {r.crop.crop_id: r.family for r in self.rulebook.rules}
            for year in range(season - lookback, season):
                entry = by_year.get(year)
                prior = planned.get(year) or (
                    entry.crop if entry and entry.status == "confirmed" else None
                )
                if prior is None:
                    add("insufficient_data", "history_unknown", "Return interval history unknown")
                elif (season - year < rule.min_return_years and prior.crop_id == crop.crop_id) or (
                    season - year < rule.family_return_years
                    and families.get(prior.crop_id) == rule.family
                ):
                    add("ineligible", "return_interval", "Crop/family return interval violated")
                elif prior.crop_id not in families and season - year < rule.family_return_years:
                    add("insufficient_data", "family_unknown", "Prior crop family is unsupported")
        if not reasons:
            add("eligible", "rules_satisfied", "All declared mandatory eligibility rules satisfied")
        priority = [
            "ineligible",
            "error",
            "unsupported",
            "out_of_domain",
            "insufficient_data",
            "eligible",
        ]
        status = next(s for s in priority if s in statuses)
        return SuitabilityAssessment(
            assessment_id=identity(
                profile.snapshot_id, crop.model_dump_json(), season, self.rulebook.version
            ),
            field_snapshot_id=profile.snapshot_id,
            season=season,
            crop=crop,
            status=status,
            rulebook_version=self.rulebook.version,
            required_capabilities=rule.required_capabilities if rule else [],
            checked_rule_ids=checked,
            unchecked_rule_ids=unchecked,
            reasons=reasons[:100],
        )

    def _validate_request(self, request):
        if request.rulebook_version != self.rulebook.version:
            raise ValueError("requested rulebook version unavailable")

    def assess(self, request):
        self._validate_request(request)
        return [
            self.evaluate(f, crop, year, request.weather, as_of=request.as_of)
            for f in request.fields
            for year in range(request.start_season, request.start_season + request.season_count)
            for crop in request.enabled_crops
        ]

    def candidates(self, request):
        self._validate_request(request)
        result = []
        for profile in request.fields:
            seasons = range(request.start_season, request.start_season + request.season_count)
            locks = {a.season: a for a in request.locks if a.field_id == profile.field_id}
            choices = [[locks[y].crop] if y in locks else request.enabled_crops for y in seasons]
            # Deterministic bounded generation; never claim exhaustive optimization.
            for crops in itertools.islice(
                itertools.product(*choices), request.solver_limits.max_candidates_per_field
            ):
                assignments, assessments = [], []
                for year, crop in zip(seasons, crops):
                    assessment = self.evaluate(
                        profile,
                        crop,
                        year,
                        request.weather,
                        history=assignments,
                        as_of=request.as_of,
                    )
                    rule = next(
                        (
                            r
                            for r in self.rulebook.rules
                            if r.crop == crop
                            and r.applies(year)
                            and r.region == self.region_for(profile)
                        ),
                        None,
                    )
                    assignment = locks.get(year) or CropAssignment(
                        field_id=profile.field_id,
                        season=year,
                        crop=crop,
                        sowing_window=rule.sowing_window if rule else None,
                        harvest_window=rule.growth_window if rule else None,
                        locked=False,
                        commitment_id=None,
                    )
                    if (
                        year in locks
                        and rule
                        and (
                            (
                                assignment.sowing_window is not None
                                and (
                                    assignment.sowing_window.start < rule.sowing_window.start
                                    or assignment.sowing_window.end > rule.sowing_window.end
                                )
                            )
                            or (
                                assignment.harvest_window is not None
                                and (
                                    assignment.harvest_window.start < rule.growth_window.start
                                    or assignment.harvest_window.end > rule.growth_window.end
                                )
                            )
                        )
                    ):
                        assessment = assessment.model_copy(
                            update={
                                "status": "ineligible",
                                "reasons": [
                                    *assessment.reasons,
                                    reason(
                                        "locked_calendar",
                                        "Committed dates outside reviewed calendar",
                                        locator=rule.source,
                                        version=self.rulebook.version,
                                    ),
                                ],
                            }
                        )
                    assignments.append(assignment)
                    assessments.append(assessment)
                result.append(
                    CandidateSequence(
                        sequence_id=identity(
                            profile.snapshot_id, *(c.model_dump_json() for c in crops)
                        ),
                        field_snapshot_id=profile.snapshot_id,
                        field_id=profile.field_id,
                        rulebook_version=self.rulebook.version,
                        assignments=assignments,
                        assessments=assessments,
                    )
                )
        return result
