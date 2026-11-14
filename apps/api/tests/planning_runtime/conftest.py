"""Explicitly synthetic frozen contracts for integration-boundary unit tests."""

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from field_shift import models as models
from field_shift.contracts.planning_v1 import ModelCard, ProfileWriteRequest
from field_shift.db import Base, build_session_factory
from field_shift.modules.access.service import Principal
from field_shift.modules.farm.models import Farm
from field_shift.modules.fields.geometry import area_hectares
from field_shift.modules.fields.models import Field
from field_shift.modules.fields.planning_profile.service import ProfileRepository
from field_shift.modules.planning_runtime.inputs import InputBundle
from field_shift.modules.planning_runtime.transport import ModelClient

ROOT = Path(__file__).resolve().parents[4]
ADMIN = Principal(None, 1, "Synthetic integration", None, "admin")
VIEWER = Principal(None, 1, "Synthetic viewer", None, "viewer")


@pytest.fixture
def example():
    return json.loads((ROOT / "contracts/planning-v1/examples/planning-contracts.json").read_text())


@pytest.fixture
def factory(tmp_path):
    factory = build_session_factory("sqlite:///" + str(tmp_path / "planning.db"))
    Base.metadata.create_all(factory.kw["bind"])
    with factory.begin() as session:
        session.add(
            Farm(
                id=1,
                name="Synthetic farm",
                season=2026,
                next_field_number=3,
                irrigation_available=False,
                priority="water",
                equipment=None,
                budget=None,
            )
        )
    yield factory
    factory.kw["bind"].dispose()


@pytest.fixture
def installed_inputs(factory, example, monkeypatch):
    request = copy.deepcopy(example["request"])
    p = request["fields"][0]
    request["farm_id"] = p["farm_id"] = "1"
    p["field_id"] = "F-01"
    p["area_ha"] = area_hectares(p["geometry"]["coordinates"][0])
    with factory.begin() as session:
        session.add(
            Field(
                id="F-01",
                farm_id=1,
                name="Synthetic field",
                crop="Wheat",
                area_ha=p["area_ha"],
                season=2026,
                geometry=p["geometry"],
                boundary_revision=p["geometry_revision"],
            )
        )
    with factory() as session:
        saved = ProfileRepository(session).write(
            "1",
            "F-01",
            ProfileWriteRequest.model_validate_json(
                json.dumps({"request_id": "init-profile", "expected_revision": None, "profile": p})
            ),
        )
    request["fields"] = [saved.model_dump(mode="json")]
    request["as_of"] = datetime.now(UTC).isoformat()
    request["weather"]["stale_after"] = "2030-01-01T00:00:00Z"
    request["weather"]["scenarios"][0]["traces"][0]["field_snapshot_id"] = saved.snapshot_id
    request["goal"] = "water_demand"
    request["economics"] = []
    request["resources"] = []
    card_data = copy.deepcopy(example["model_card"])
    card_data["promotion"] = "baseline_only"
    card = ModelCard.model_validate_json(json.dumps(card_data))
    monkeypatch.setattr(ModelClient, "card", lambda self, bundle: card)
    bundle = InputBundle.model_validate_json(
        json.dumps(
            {
                "template": request,
                "rulebook": {"version": request["rulebook_version"], "rules": []},
                "regions": {"F-01": "synthetic"},
                "hydraulics": {saved.snapshot_id: {"synthetic": True}},
                "footprint": None,
                "provenance": "Synthetic transport test only",
                "synthetic": True,
            }
        )
    )
    return bundle, card
