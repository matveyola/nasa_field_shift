"""Require current, explicitly linked and complete land evidence.

land_source_ids links the land assertion to SourceRecords; unrelated soil/weather
records cannot supply missing land coverage. Empty legacy links remain unknown.
Only valid, full polygon/area-weighted coverage qualifies; partial, missing or
point-only records never imply whole-field arability. Source metadata are
provenance assertions (including user-entered evidence), not independent source
verification or crop suitability validation. The existing conservative synthetic,
rejected, geometry, scope and availability vetoes remain across the snapshot.
"""

from datetime import UTC, datetime

from field_shift_science.fields.land import qualified_land_sources

from field_shift.modules.fields.planning_profile.service import ProfileError, ProfileRepository


def land_status(session, field, *, now=None):
    now = now or datetime.now(UTC)
    try:
        profile = ProfileRepository(session).latest(str(field.farm_id), field.id)
    except ProfileError:
        return "unknown"
    if (
        profile.farm_id != str(field.farm_id)
        or profile.field_id != field.id
        or profile.geometry_revision != field.boundary_revision
    ):
        return "unknown"
    if profile.geometry.model_dump(mode="json", exclude={"crs"}) != {
        key: field.geometry[key] for key in ("type", "coordinates")
    }:
        return "unknown"
    if profile.as_of > now or any(
        s.available_at > now
        or s.retrieved_at > now
        or s.kind == "synthetic"
        or s.quality == "rejected"
        for s in profile.sources
    ):
        return "unknown"
    bound_land_sources = qualified_land_sources(profile, as_of=now)
    if not bound_land_sources:
        return "unknown"
    return profile.land_status
