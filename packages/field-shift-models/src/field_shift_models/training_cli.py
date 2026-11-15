"""Explicit offline manifest handoff. Never used by API inference."""

import json
from datetime import datetime

from field_shift_contracts.planning_v1.models import TrainingObservation

from .learning import Gate, Row, residual_benchmark
from .provenance import digest


def train_manifest(path):
    raw = json.loads(path.read_text())
    protocol = dict(raw["gate"])
    protocol["frozen_at"] = datetime.fromisoformat(protocol["frozen_at"])
    gate = Gate(**protocol)
    if raw["gate_sha256"] != gate.checksum:
        raise ValueError("Protocol differs from predeclared gate checksum")
    rows = []
    for item in raw["rows"]:
        item = dict(item)
        item["observation"] = TrainingObservation.model_validate_json(
            json.dumps(item["observation"])
        )
        for key in ("issue_at", "predictor_available_at", "cycle_at"):
            item[key] = datetime.fromisoformat(item[key])
        item["features"] = tuple(item["features"])
        rows.append(Row(**item))
    result = residual_benchmark(
        rows, gate, holdout_opened_at=datetime.fromisoformat(raw["holdout_opened_at"])
    )
    return {
        "input_sha256": digest(raw),
        "report": result,
        "release": "Offline candidate evidence only; no serving artifact changed",
    }
