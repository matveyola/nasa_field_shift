"""Synthetic contract examples only; no actual field observations."""

import json
from pathlib import Path

import pytest
from field_shift_contracts.planning_v1 import FieldProfileSnapshot

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def profile():
    document = json.loads(
        (ROOT / "contracts/planning-v1/examples/planning-contracts.json").read_text()
    )
    return FieldProfileSnapshot.model_validate_json(json.dumps(document["field_profile"]))
