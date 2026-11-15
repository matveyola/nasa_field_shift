"""Shared evidence qualification, not independent land or crop validation.

Bump the policy version when admission semantics change. Trusted planning records
this authoritative version so new application of old results requires replanning.
Inputs are structural profile/source objects; this package does not depend on the
planning contract distribution. Synthetic evidence needs an explicit caller gate.
"""

LAND_ELIGIBILITY_POLICY_VERSION = "explicit-land-evidence-v1"


def qualified_land_sources(profile, *, as_of=None, allow_synthetic=False):
    cutoff = profile.as_of if as_of is None else as_of
    if profile.as_of > cutoff:
        return []
    bound = set(profile.land_source_ids)
    return [
        source
        for source in profile.sources
        if source.id in bound
        and (allow_synthetic or source.kind != "synthetic")
        and source.quality == "valid"
        and source.coverage_fraction == 1
        and source.spatial_method in {"polygon", "area_weighted"}
        and source.available_at <= profile.as_of
        and source.retrieved_at <= cutoff
    ]
