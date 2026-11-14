"""Limits apply before importing the numerical runtime."""

import json
import resource
import sys
from contextlib import redirect_stdout

resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024**2, 16 * 1024**2))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run(operation, payload):
    from datetime import date

    from field_shift_models.engine import Parameters
    from field_shift_models.provider import AquaCropProvider

    from field_shift.contracts.planning_v1 import (
        CandidateSequence,
        FieldProfileSnapshot,
        WeatherScenarioSet,
    )

    parameters = {
        key: Parameters(
            tuple(p["ksat_mm_day"]),
            tuple(p["penetrability_percent"]),
            p["source"],
            p["synthetic"],
            tuple((date.fromisoformat(d), v) for d, v in p.get("irrigation_events", [])),
            p["uniform_initial_profile"],
        )
        for key, p in payload["hydraulics"].items()
    }
    provider = AquaCropProvider(parameters, payload.get("footprint"))
    if operation == "card":
        return provider.model_card().model_dump(mode="json")
    if operation != "evaluate":
        raise ValueError("Unknown operation")
    return provider.evaluate(
        FieldProfileSnapshot.model_validate_json(json.dumps(payload["profile"])),
        CandidateSequence.model_validate_json(json.dumps(payload["candidate"])),
        WeatherScenarioSet.model_validate_json(json.dumps(payload["weather"])),
    ).model_dump(mode="json")


if __name__ == "__main__":
    raw = sys.stdin.buffer.read(16 * 1024**2 + 1)
    if len(raw) > 16 * 1024**2:
        raise SystemExit(1)
    try:
        with redirect_stdout(sys.stderr):
            output = run(sys.argv[1], json.loads(raw))
        print(json.dumps(output, allow_nan=False))
    except Exception as exc:
        print(type(exc).__name__, file=sys.stderr)
        raise SystemExit(1) from None
