"""Use only current, available, nonsynthetic profile evidence for the land gate."""

from datetime import UTC, datetime

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
        s.available_at > now or s.kind == "synthetic" or s.quality == "rejected"
        for s in profile.sources
    ):
        return "unknown"
    return profile.land_status
