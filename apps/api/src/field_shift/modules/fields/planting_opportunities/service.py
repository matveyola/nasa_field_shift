"""Current field scope and reviewed calendars, with historical references as fallback."""

from datetime import UTC, date, datetime, time

from field_shift_science.crops.calendar import maturity_year, month_period, rolling_end
from field_shift_science.fields.schemas import Polygon
from field_shift_science.geo_ml.era5_land import field_location
from sqlalchemy import select

from field_shift.errors import NotFoundError
from field_shift.modules.fields.models import Field
from field_shift.modules.fields.planning_profile.service import ProfileError, ProfileRepository
from field_shift.modules.planning_runtime.inputs import decode_bundle
from field_shift.modules.planning_runtime.models import PlanningBundle, PlanningHead

from .reference import reference_at
from .schemas import Opportunities, Opportunity, Period, Source

CROPS = ("Wheat", "Maize", "Soy", "Barley", "Sunflower", "Rapeseed", "Peas")
NATIVE_PROFILES = frozenset({"Wheat", "Maize", "Soy", "Barley", "Sunflower"})
MISSING = [
    "field_weather",
    "soil_workability",
    "preceding_crop_harvest",
    "cultivar_maturity",
    "economics",
]


def period(start, end):
    return Period(start=start, end=end)


def status(bounds, planning_date, *, reviewed):
    if bounds.end < planning_date:
        return "passed"
    if bounds.start > planning_date:
        return "upcoming"
    return "open" if reviewed else "current_reference_month"


def profile_for(session, farm_id, field, knowledge_as_of):
    try:
        profile = ProfileRepository(session).latest(str(farm_id), field.id)
    except ProfileError as error:
        return None, "missing" if error.code == "not_found" else error.code
    if profile.geometry_revision != field.boundary_revision or profile.geometry.model_dump(
        mode="json", exclude={"crs"}
    ) != {key: field.geometry[key] for key in ("type", "coordinates")}:
        return None, "mismatched_geometry"
    if profile.farm_id != str(farm_id) or profile.field_id != field.id:
        return None, "mismatched_scope"
    if profile.as_of > knowledge_as_of or any(
        s.available_at > knowledge_as_of for s in profile.sources
    ):
        return None, "not_available_as_of"
    if any(s.kind == "synthetic" or s.quality == "rejected" for s in profile.sources):
        return None, "untrusted_sources"
    return profile, "current"


def reviewed_rows(session, farm_id, field, profile, knowledge_as_of, planning_date, end):
    head = session.get(PlanningHead, farm_id)
    if head is None:
        return [], []
    row = session.get(PlanningBundle, head.bundle_id)
    if row is None or row.farm_id != farm_id:
        return [], ["reviewed_bundle_unavailable"]
    if len(row.document.get("rulebook", {}).get("rules", [])) > 512:
        return [], ["reviewed_rule_count_exceeds_bound"]
    try:
        bundle = decode_bundle(row)
    except ValueError:
        return [], ["reviewed_bundle_invalid"]
    if bundle.synthetic:
        return [], ["synthetic_bundle_not_used"]
    if profile is None or not any(p == profile for p in bundle.template.fields):
        return [], ["reviewed_bundle_profile_stale"]
    if bundle.template.as_of > knowledge_as_of:
        return [], ["reviewed_bundle_not_available_as_of"]
    notes = []
    if bundle.template.weather.stale_after <= knowledge_as_of:
        notes.append("weather_issue_expired_no_suitability_inferred")
    result = []
    for rule in bundle.rulebook.rules:
        if (
            rule.crop.crop_id not in CROPS
            or rule.review_status != "reviewed"
            or rule.purpose != "new_sowing"
            or rule.region != bundle.regions.get(field.id)
            or rule.sowing_window.end < date(planning_date.year, 1, 1)
            or rule.sowing_window.start > end
        ):
            continue
        result.append(
            Opportunity(
                id="reviewed-" + rule.id,
                crop_id=rule.crop.crop_id,
                cycle_id=rule.crop.cultivar_id,
                system="/".join(rule.irrigation_systems),
                calendar_kind="reviewed_window",
                date_precision="day",
                calendar_status=status(rule.sowing_window, planning_date, reviewed=True),
                planting_period=period(rule.sowing_window.start, rule.sowing_window.end),
                maturity_period=period(rule.growth_window.end, rule.growth_window.end),
                maturity_basis="reviewed_growth_window_end",
                harvest_year=rule.growth_window.end.year,
                next_reference_date=max(planning_date, rule.sowing_window.start)
                if rule.sowing_window.end >= planning_date
                else None,
                eligibility="not_assessed",
                missing_data=list(MISSING),
                reasons=[
                    (
                        "Reviewed calendar only; full agronomic eligibility and profitability "
                        "were not evaluated."
                    )
                ],
                source=Source(
                    kind="reviewed_window",
                    version=bundle.rulebook.version,
                    source_url=rule.source,
                    checksum_sha256=row.id,
                    available_at=bundle.template.as_of,
                ),
                native_crop_profile_available=rule.crop.crop_id in NATIVE_PROFILES,
            )
        )
    return result, notes


def unavailable(crop, reason, source=None, cycle=None):
    return Opportunity(
        id=f"unavailable-{crop}-{cycle or 'none'}",
        crop_id=crop,
        cycle_id=cycle,
        system="rainfed",
        calendar_kind="unavailable",
        date_precision="unknown",
        calendar_status="unknown",
        planting_period=None,
        maturity_period=None,
        harvest_year=None,
        maturity_basis="unknown",
        next_reference_date=None,
        eligibility="unavailable",
        missing_data=["crop_calendar", *MISSING],
        reasons=[reason],
        source=source,
        native_crop_profile_available=crop in NATIVE_PROFILES,
    )


def opportunities(session, principal, field_id, planning_date, year_count, *, now=None):
    now = now or datetime.now(UTC)
    end = rolling_end(planning_date, year_count)
    knowledge_as_of = min(now, datetime.combine(planning_date, time.max, UTC))
    field = session.scalar(
        select(Field).where(Field.id == field_id, Field.farm_id == principal.farm_id)
    )
    if field is None:
        raise NotFoundError("Field was not found")
    profile, profile_status = profile_for(session, principal.farm_id, field, knowledge_as_of)
    items, notes = reviewed_rows(
        session, principal.farm_id, field, profile, knowledge_as_of, planning_date, end
    )
    reviewed_scope = {(r.crop_id, r.harvest_year) for r in items}
    latitude, longitude = field_location(Polygon.model_validate(field.geometry))
    calendars, cell, receipt = reference_at(latitude, longitude)
    source = Source(
        kind="regional_typical_month",
        version=receipt["version"],
        source_url=receipt["source_url"],
        license=receipt["license"],
        checksum_sha256=receipt["artifact_sha256"],
        available_at=datetime.fromisoformat(receipt["available_at"].replace("Z", "+00:00")),
        reference_year=receipt["reference_year"],
        resolution_arc_minutes=receipt["resolution_arc_minutes"],
        grid_latitude=cell["latitude"] if cell else None,
        grid_longitude=cell["longitude"] if cell else None,
    )
    for crop in CROPS:
        native = [c for c in calendars if c["crop_id"] == crop]
        if source.available_at > knowledge_as_of:
            if not any(r.crop_id == crop for r in items):
                items.append(
                    unavailable(
                        crop, "Calendar source was not available at the planning knowledge cutoff."
                    )
                )
            continue
        if not native:
            if not any(r.crop_id == crop for r in items):
                items.append(
                    unavailable(
                        crop,
                        (
                            "No exact crop calendar category or native grid cell is available; "
                            "no species substitution."
                        ),
                        source,
                    )
                )
            continue
        for calendar in native:
            sow, harvest = calendar["planting_month"], calendar["maturity_month"]
            if sow is None:
                if not any(r.crop_id == crop for r in items):
                    items.append(
                        unavailable(
                            crop,
                            (
                                "No reported crop calendar in the containing source cell; this "
                                "is not proof of unsuitable land."
                            ),
                            source,
                            calendar["cycle_id"],
                        )
                    )
                continue
            for year in range(planning_date.year, end.year + 1):
                sow_start, sow_end = month_period(year, sow)
                if sow_start > end:
                    continue
                harvest_year = maturity_year(year, sow, harvest) if harvest else None
                if (crop, harvest_year) in reviewed_scope:
                    continue
                maturity = period(*month_period(harvest_year, harvest)) if harvest_year else None
                reasons = [
                    (
                        "Historical administrative-unit planting month, not an admissible sowing "
                        "window or plant-now recommendation."
                    )
                ]
                if harvest_year is None:
                    reasons.append(
                        "Maturity year is ambiguous or missing; no crop duration was invented."
                    )
                else:
                    reasons.append(
                        "Maturity year follows the next annual occurrence of the source "
                        "month; this is reference calendar arithmetic, not a harvest forecast."
                    )
                items.append(
                    Opportunity(
                        id=f"reference-{calendar['cycle_id']}-{year}",
                        crop_id=crop,
                        cycle_id=calendar["cycle_id"],
                        system="rainfed",
                        calendar_kind="regional_typical_month",
                        date_precision="month",
                        calendar_status=status(
                            period(sow_start, sow_end), planning_date, reviewed=False
                        ),
                        planting_period=period(sow_start, sow_end),
                        maturity_period=maturity,
                        maturity_basis="regional_maturity_month" if maturity else "unknown",
                        harvest_year=harvest_year,
                        next_reference_date=max(planning_date, sow_start)
                        if sow_end >= planning_date
                        else None,
                        eligibility="review_required",
                        missing_data=list(MISSING),
                        reasons=reasons,
                        source=source,
                        native_crop_profile_available=crop in NATIVE_PROFILES,
                    )
                )
    for item in items:
        if profile is None:
            item.missing_data.append("current_field_profile")
        if profile is None or profile.land_status == "unknown":
            item.missing_data.append("arable_land_evidence")
        elif profile.land_status != "arable":
            item.eligibility = "ineligible"
            item.reasons.append(
                "Current sourced field profile identifies non-arable, ocean or ice land."
            )
        if item.calendar_kind == "reviewed_window" and item.calendar_status == "passed":
            item.eligibility = "ineligible"
            item.reasons.append(
                "This reviewed sowing window has passed; other calendars are assessed separately."
            )
    items.sort(
        key=lambda item: (
            item.eligibility in {"ineligible", "unavailable"},
            item.next_reference_date is None,
            item.next_reference_date
            or (item.planting_period.start if item.planting_period else date.max),
            item.calendar_kind != "reviewed_window",
            item.crop_id,
            item.id,
        )
    )
    limitations = [
        "Ranked by calendar chronology, not suitability, profit or calibrated weather probability.",
        (
            "Month endpoints describe reference precision; they are not validated sowing-window "
            "bounds."
        ),
        (
            "Rainfed 2020 administrative references do not establish cultivation or eligibility "
            "on this field."
        ),
        (
            "Current field geometry is sampled; selecting a past date does not reconstruct "
            "historical field state."
        ),
        "Native crop profile availability is not field validation or support for every crop cycle.",
        *notes,
    ]
    if len(items) > 256:
        limitations.append("Result capped at 256 chronologically ordered calendar entries.")
    return Opportunities(
        field_id=field.id,
        geometry_revision=field.boundary_revision,
        profile_revision=profile.profile_revision if profile else None,
        profile_status=profile_status,
        planning_date=planning_date,
        generated_at=now,
        knowledge_as_of=knowledge_as_of,
        year_count=year_count,
        lookahead_end=end,
        items=items[:256],
        limitations=limitations,
    )
