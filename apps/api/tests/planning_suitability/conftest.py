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
    profile = document["field_profile"]
    land = dict(
        profile["sources"][0],
        id="synthetic-land",
        product="controlled synthetic land assumption",
        kind="synthetic",
        spatial_method="polygon",
        coverage_fraction=1.0,
        quality="valid",
    )
    profile["sources"].append(land)
    profile["land_source_ids"] = [land["id"]]
    return FieldProfileSnapshot.model_validate_json(json.dumps(profile))


@pytest.fixture
def examples():
    return json.loads((ROOT / "contracts/planning-v1/examples/planning-contracts.json").read_text())


@pytest.fixture
def fixtures():
    return json.loads((ROOT / "contracts/planning-v1/weather-fixtures.generated.json").read_text())
