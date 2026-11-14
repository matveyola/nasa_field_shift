"""Transactional admission, bootstrap and evidence adapters."""

from datetime import UTC, datetime

from sqlalchemy import select

from field_shift.contracts.planning_v1 import FieldProfileSnapshot, ProfileWriteRequest
from field_shift.modules.fields.models import Field
from field_shift.modules.fields.planning_profile.service import ProfileError, ProfileRepository
from field_shift.modules.rotations.scenario_planning.common import canonical, reason
from field_shift.modules.rotations.scenario_planning.models import PlanningCommitment
from field_shift.modules.rotations.scenario_planning.service import PlanningFailure, RunService

from .inputs import TrustedResolver, decode_bundle, fresh_template, get_bundle
from .models import PlanningDelivery


class RuntimeService(RunService):
    def __init__(self):
        super().__init__(TrustedResolver())

    def submit(self, session, principal, request):
        run = super().submit(session, principal, request)
        if run.status == "queued" and session.get(PlanningDelivery, run.run_id) is None:
            session.add(PlanningDelivery(run_id=run.run_id, next_at=datetime.now(UTC)))
            session.flush()
        return run

    def evidence(self, session, principal, farm_id, run_id):
        self.scope(session, principal, farm_id)
        row = self._row(session, farm_id, run_id)
        data = row.artifacts or {}
        return {
            "candidates": data.get("candidates", []),
            "evaluations": data.get("evaluations", []),
            "model_card": data.get("model_card"),
            "sha256": data.get("sha256"),
        }

    def bootstrap(self, session, principal):
        self.scope(session, principal, str(principal.farm_id))
        fields = list(
            session.scalars(
                select(Field).where(Field.farm_id == principal.farm_id).order_by(Field.id)
            )
        )
        profiles, missing = [], []
        for f in fields:
            try:
                profiles.append(ProfileRepository(session).latest(str(principal.farm_id), f.id))
            except ProfileError as exc:
                missing.append({"field_id": f.id, "name": f.name, "code": exc.code})
        result = {
            "farm_id": str(principal.farm_id),
            "profiles": profiles,
            "missing_profiles": missing,
            "request": None,
            "crop_options": [],
            "model_card": None,
            "configuration": None,
            "status": "missing_inputs",
            "reason": "Reviewed weather, rules and model parameters are missing.",
        }
        try:
            row = get_bundle(session, principal.farm_id)
            bundle = decode_bundle(row)
            template = fresh_template(bundle)
            self.resolver.resolve(session, template, lock=True)
            commitments = list(
                session.scalars(
                    select(PlanningCommitment).where(
                        PlanningCommitment.farm_id == principal.farm_id
                    )
                )
            )
            data = template.model_dump(mode="json")
            locks = {(a["field_id"], a["season"]): a for a in data["locks"]}
            for c in commitments:
                if (
                    c.field_id in {f.field_id for f in template.fields}
                    and template.start_season
                    <= c.season
                    < template.start_season + template.season_count
                ):
                    locks[(c.field_id, c.season)] = c.assignment
            data["locks"] = list(locks.values())
            from field_shift.contracts.planning_v1 import FarmPlanningRequest

            template = FarmPlanningRequest.model_validate_json(canonical(data))
            result.update(
                request=template,
                crop_options=template.enabled_crops,
                model_card=row.model_card,
                configuration=row.id,
                status="synthetic" if bundle.synthetic else "baseline_only",
                reason="Scenario baseline; no independently validated yield accuracy.",
            )
        except (PlanningFailure, ValueError) as exc:
            result.update(status=getattr(exc, "code", "stale"), reason=str(exc))
        return result

    def initialize_profile(self, session, principal, field_id):
        self.scope(session, principal, str(principal.farm_id), write=True)
        field = session.scalar(
            select(Field)
            .where(Field.id == field_id, Field.farm_id == principal.farm_id)
            .with_for_update()
        )
        if field is None:
            raise PlanningFailure("not_found", "Field is inaccessible")
        repo = ProfileRepository(session)
        try:
            return repo.latest(str(principal.farm_id), field_id)
        except ProfileError as exc:
            if exc.code != "not_found":
                raise PlanningFailure(exc.code, exc.message) from exc
        from uuid import uuid4

        timestamp = datetime.now(UTC)
        profile = FieldProfileSnapshot.model_validate_json(
            canonical(
                {
                    "snapshot_id": str(uuid4()),
                    "farm_id": str(principal.farm_id),
                    "field_id": field.id,
                    "profile_revision": 1,
                    "geometry_revision": field.boundary_revision,
                    "as_of": timestamp.isoformat(),
                    "geometry": {**field.geometry, "crs": "EPSG:4326"},
                    "area_ha": field.area_ha,
                    "land_status": "unknown",
                    "history": [],
                    "history_complete_since": None,
                    "current_crop": None,
                    "soil": [],
                    "water_state": None,
                    "management": {
                        "production_system": "outdoor",
                        "irrigation": [],
                        "equipment_ids": [],
                        "residue_cover_fraction": None,
                        "tillage": "unknown",
                    },
                    "terrain": None,
                    "hazards": [],
                    "sources": [],
                    "missing_data": [
                        reason(
                            "profile_incomplete",
                            "Supply field history, soil, water and land evidence.",
                            field.id,
                            "source",
                        ).model_dump(mode="json")
                    ],
                }
            )
        )
        return repo.write(
            str(principal.farm_id),
            field_id,
            ProfileWriteRequest(request_id=str(uuid4()), expected_revision=None, profile=profile),
        )


service = RuntimeService()
