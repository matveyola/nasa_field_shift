"""Trusted farm inputs, never an echo of an untrusted request."""

from datetime import UTC, datetime
from uuid import uuid4

from field_shift_contracts.planning_v1 import FarmPlanningRequest
from field_shift_contracts.planning_v1.common import Contract
from field_shift_science.fields.land import LAND_ELIGIBILITY_POLICY_VERSION
from pydantic import Field as PydanticField
from pydantic import model_validator
from sqlalchemy import select

from field_shift.modules.fields.models import Field
from field_shift.modules.fields.planning_profile.service import ProfileError, ProfileRepository
from field_shift.modules.fields.planning_profile.weather_archive import preserve_issue
from field_shift.modules.rotations.scenario_planning.common import canonical, digest
from field_shift.modules.rotations.scenario_planning.service import PlanningFailure
from field_shift.modules.rotations.suitability.rules import Rulebook

from .calendars import Calendar
from .models import PlanningBundle, PlanningHead
from .transport import ModelClient, ModelUnavailable


class InputBundle(Contract):
    template: FarmPlanningRequest
    rulebook: Rulebook
    regions: dict[str, str]
    hydraulics: dict[str, dict]
    footprint: tuple[float, float, float, float] | None
    provenance: str = PydanticField(min_length=1, max_length=2000)
    synthetic: bool
    calendars: list[Calendar] = PydanticField(default_factory=list, max_length=300)

    @model_validator(mode="after")
    def scope(self):
        if self.rulebook.version != self.template.rulebook_version:
            raise ValueError("Rulebook version differs from template")
        if set(self.regions) != {f.field_id for f in self.template.fields}:
            raise ValueError("Every field requires an explicit region")
        if set(self.hydraulics) != {f.snapshot_id for f in self.template.fields}:
            raise ValueError("Hydraulics must identify every exact field snapshot")
        if not self.synthetic and (
            self.template.weather.scenario_type == "synthetic"
            or any(s.kind == "synthetic" for f in self.template.fields for s in f.sources)
            or any(s.kind == "synthetic" for s in self.template.weather.sources)
            or any(r.review_status != "reviewed" for r in self.rulebook.rules)
            or any(p.get("synthetic") is not False for p in self.hydraulics.values())
        ):
            raise ValueError(
                "Production inputs require reviewed rules and non-synthetic provenance"
            )
        if self.synthetic and self.template.weather.scenario_type != "synthetic":
            raise ValueError("Demonstration forcing must be visibly synthetic")
        if self.footprint and not (
            -180 <= self.footprint[0] < self.footprint[2] <= 180
            and -90 <= self.footprint[1] < self.footprint[3] <= 90
        ):
            raise ValueError("Invalid footprint")
        keys = [(c.field_id, c.crop.model_dump_json(), c.season) for c in self.calendars]
        if len(keys) != len(set(keys)) or any(
            c.field_id not in self.regions for c in self.calendars
        ):
            raise ValueError("Duplicate or foreign operation calendar")
        return self


def decode_bundle(row):
    return InputBundle.model_validate_json(canonical(row.document))


def get_bundle(session, farm_id, *, lock=False):
    query = select(PlanningHead).where(PlanningHead.farm_id == int(farm_id))
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    head = session.scalar(query)
    if head is None:
        raise PlanningFailure(
            "unavailable", "Reviewed weather, rules and model parameters are missing."
        )
    return session.get(PlanningBundle, head.bundle_id)


def current_profiles(session, request):
    profiles = []
    repo = ProfileRepository(session)
    # All writers use the existing field lock. Sorted acquisition avoids field-order deadlocks.
    for f in sorted(request.fields, key=lambda f: f.field_id):
        field = session.scalar(
            select(Field)
            .where(Field.id == f.field_id, Field.farm_id == int(request.farm_id))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if field is None:
            raise PlanningFailure("not_found", "Field is inaccessible")
        try:
            profile = repo.latest(request.farm_id, f.field_id)
        except ProfileError as exc:
            raise PlanningFailure(exc.code, exc.message) from exc
        if profile != f:
            raise PlanningFailure("stale", "Profile or boundary changed; rebuild input snapshots")
        profiles.append(profile)
    return profiles


def install_bundle(session, bundle, principal, service, client=None):
    service.scope(session, principal, bundle.template.farm_id, write=True)
    if principal.role != "admin":
        raise PlanningFailure("forbidden", "Only an administrator can install reviewed inputs")
    current_profiles(session, bundle.template)
    card = (client or ModelClient()).card(bundle)
    if card.promotion not in {"baseline_only", "passed"}:
        raise PlanningFailure("unavailable", "Model release gate does not permit serving")
    preserve_issue(session, principal.farm_id, bundle.template.weather)
    key = digest(
        {"bundle": bundle.model_dump(mode="json"), "model_card": card.model_dump(mode="json")}
    )
    if session.get(PlanningBundle, key) is None:
        session.add(
            PlanningBundle(
                id=key,
                farm_id=principal.farm_id,
                document=bundle.model_dump(mode="json"),
                model_card=card.model_dump(mode="json"),
            )
        )
        session.flush()
    head = session.get(PlanningHead, principal.farm_id)
    if head:
        head.bundle_id = key
    else:
        session.add(PlanningHead(farm_id=principal.farm_id, bundle_id=key))
    session.flush()
    return key


class TrustedResolver:
    def resolve(self, session, request, *, lock):
        row = get_bundle(session, request.farm_id, lock=lock)
        bundle = decode_bundle(row)
        current_profiles(session, request)
        try:
            live_card = ModelClient().card(bundle)
        except ModelUnavailable as exc:
            raise PlanningFailure("unavailable", str(exc)) from exc
        if live_card.model_dump(mode="json") != row.model_card:
            raise PlanningFailure("stale", "Model artifact changed; review input bundle again")
        selected = {f.snapshot_id for f in request.fields}
        expected = bundle.template.weather.model_copy(deep=True)
        for s in expected.scenarios:
            s.traces = [t for t in s.traces if t.field_snapshot_id in selected]
        if (
            request.weather != expected
            or request.rulebook_version != bundle.rulebook.version
            or not selected <= {f.snapshot_id for f in bundle.template.fields}
            or any(c not in bundle.template.enabled_crops for c in request.enabled_crops)
        ):
            raise PlanningFailure(
                "stale", "Weather, rules, crop parameters or field inputs changed"
            )
        if request.weather.stale_after <= datetime.now(UTC):
            raise PlanningFailure("stale", "Weather issue expired; acquire a new issue")
        return request, {
            "eligibility_policy": LAND_ELIGIBILITY_POLICY_VERSION,
            "profiles": {f.field_id: f.snapshot_id for f in request.fields},
            "geometry": {f.field_id: f.geometry_revision for f in request.fields},
            "weather": digest(expected),
            "rulebook": digest(bundle.rulebook),
            "cultivars": digest([c.model_dump(mode="json") for c in bundle.template.enabled_crops]),
            "models": digest(row.model_card),
            "bundle": row.id,
        }


def fresh_template(bundle):
    data = bundle.template.model_dump(mode="json")
    data.update(request_id=str(uuid4()), as_of=datetime.now(UTC).isoformat())
    return FarmPlanningRequest.model_validate_json(canonical(data))
