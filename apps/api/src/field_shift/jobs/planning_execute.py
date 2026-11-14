"""One isolated, read-only planning execution from a durable immutable run."""

import json
import sys

from field_shift.config import get_settings
from field_shift.contracts.planning_v1 import ModelCard
from field_shift.db import build_session_factory
from field_shift.modules.fields.planning_profile.service import ProfileRepository
from field_shift.modules.planning_runtime.calendars import CalendarSuitability
from field_shift.modules.planning_runtime.inputs import decode_bundle
from field_shift.modules.planning_runtime.models import PlanningBundle
from field_shift.modules.planning_runtime.service import service
from field_shift.modules.planning_runtime.transport import RemoteCropProvider
from field_shift.modules.rotations.scenario_planning.common import canonical
from field_shift.modules.rotations.scenario_planning.execution import PlanningEngine
from field_shift.modules.rotations.scenario_planning.models import PlanningRunRow


def execute(session, identifier):
    row = session.get(PlanningRunRow, identifier)
    if row is None or row.status != "running":
        raise ValueError("Run has no active attempt")
    stored = session.get(PlanningBundle, row.versions["bundle"])
    if stored is None or stored.farm_id != row.farm_id:
        raise ValueError("Missing pinned inputs")
    bundle = decode_bundle(stored)
    card = ModelCard.model_validate_json(canonical(stored.model_card))
    engine = PlanningEngine(
        ProfileRepository(session),
        CalendarSuitability(bundle),
        RemoteCropProvider(bundle, card),
    )
    comparison, artifacts = engine.execute(service.read(row).request)
    return {"comparison": comparison.model_dump(mode="json"), "artifacts": artifacts}


def main():
    try:
        with build_session_factory(get_settings().database_url)() as session:
            result = execute(session, sys.argv[1])
        print(json.dumps(result, allow_nan=False))
    except Exception as exc:
        # Avoid dumping private field inputs from validation exception reprs.
        print(type(exc).__name__, file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
