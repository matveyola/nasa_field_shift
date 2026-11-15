"""Bounded private process protocol and reproducible offline benchmark commands."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 16 * 1024 * 1024


def limits():
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def bounded_worker(operation, payload):
    data = json.dumps(payload, allow_nan=False)
    if len(data.encode()) > MAX_BYTES:
        raise ValueError("Private input exceeds 16 MiB")
    env = dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    result = subprocess.run(
        [sys.executable, "-m", "field_shift_models.cli", "_worker", operation],
        input=data,
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
        preexec_fn=limits,
        check=True,
    )
    if len(result.stdout.encode()) > MAX_BYTES:
        raise ValueError("Private output exceeds 16 MiB")
    return json.loads(result.stdout)


def worker(operation, payload):
    import resource
    import time

    start = time.perf_counter()
    if operation == "evaluate":
        from datetime import date

        from field_shift_contracts.planning_v1.field import CandidateSequence, FieldProfileSnapshot
        from field_shift_contracts.planning_v1.weather import WeatherScenarioSet

        from .engine import Parameters
        from .provider import AquaCropProvider

        profile = FieldProfileSnapshot.model_validate_json(json.dumps(payload["profile"]))
        params = payload["hydraulics"]
        hydraulics = Parameters(
            tuple(params["ksat_mm_day"]),
            tuple(params["penetrability_percent"]),
            params["source"],
            params["synthetic"],
            tuple((date.fromisoformat(d), v) for d, v in params.get("irrigation_events", [])),
            params.get("uniform_initial_profile", False),
        )
        provider = AquaCropProvider({profile.snapshot_id: hydraulics}, payload.get("footprint"))
        value = provider.evaluate(
            profile,
            CandidateSequence.model_validate_json(json.dumps(payload["candidate"])),
            WeatherScenarioSet.model_validate_json(json.dumps(payload["weather"])),
        )
        output = {
            "evaluation": value.model_dump(mode="json"),
            "model_card": provider.model_card().model_dump(mode="json"),
        }
    elif operation == "wofost":
        from .comparison import wofost_synthetic

        output = wofost_synthetic(**payload)
    elif operation == "reference":
        from .comparison import reference_bucket

        output = reference_bucket(**payload)
    else:
        raise ValueError("Unknown private operation")
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "result": output,
        "resources": {
            "wall_seconds": time.perf_counter() - start,
            "cpu_seconds": usage.ru_utime + usage.ru_stime,
            "peak_rss_kib": usage.ru_maxrss,
        },
    }


def benchmark():
    import platform

    from .fixtures import synthetic_inputs
    from .provenance import digest, distribution_manifest

    report = {
        "report_version": "crop-cpu-v1",
        "kind": "synthetic_contract",
        "empirical_validation": "insufficient_training_data",
        "accuracy_metrics": None,
        "promotion": "baseline_only",
        "python": platform.python_version(),
        "engines": [
            {k: v for k, v in distribution_manifest(n).items() if k != "files"}
            for n in ("aquacrop", "pcse")
        ],
        "runs": {},
    }
    for crop in ("Maize", "Barley"):
        for dry in (False, True):
            profile, candidate, weather, parameters = synthetic_inputs(crop=crop, dry=dry)
            payload = {
                "profile": profile.model_dump(mode="json"),
                "candidate": candidate.model_dump(mode="json"),
                "weather": weather.model_dump(mode="json"),
                "hydraulics": vars(parameters),
            }
            key = crop + ("-dry" if dry else "-temperate")
            report["runs"][key] = {
                "input_sha256": digest(payload),
                "ospy": summarize_ospy(bounded_worker("evaluate", payload)),
                "wofost": bounded_worker("wofost", {"crop": crop, "dry": dry}),
                "reference": bounded_worker("reference", {"dry": dry}),
            }
    return report


def summarize_ospy(run):
    from .provenance import digest

    evaluation = run["result"]["evaluation"]
    return {
        "resources": run["resources"],
        "result": {
            "evaluation_sha256": digest(evaluation),
            "model": evaluation["model"],
            "input_sha256": evaluation["input_sha256"],
            "applicability": evaluation["applicability"],
            "seasons": [
                {
                    k: s[k]
                    for k in (
                        "season",
                        "status",
                        "harvest_basis",
                        "opening_state_id",
                        "closing_state_id",
                    )
                }
                | {
                    k: s[k]["value"]
                    for k in (
                        "yield_t_ha",
                        "root_zone_reserve_mm",
                        "surface_moisture_m3_m3",
                        "stress_days",
                        "irrigation_mm",
                    )
                }
                for s in evaluation["seasons"]
            ],
            "uncertainty_kind": "none",
            "validated_scope": None,
        },
    }


def stable_report(value):
    """Timing/RSS vary between executions; all model/input/numeric results must reproduce."""
    if isinstance(value, dict):
        return {k: stable_report(v) for k, v in value.items() if k != "resources"}
    if isinstance(value, list):
        return [stable_report(v) for v in value]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("benchmark")
    b.add_argument("--output", type=Path)
    b.add_argument("--check", type=Path)
    sub.add_parser("evaluate")
    ingest = sub.add_parser("ingest")
    ingest.add_argument("observations", type=Path)
    train = sub.add_parser("train")
    train.add_argument("manifest", type=Path)
    w = sub.add_parser("_worker")
    w.add_argument("operation", choices=["evaluate", "wofost", "reference"])
    args = parser.parse_args()
    if args.command in ("evaluate", "_worker"):
        payload = sys.stdin.buffer.read(MAX_BYTES + 1)
        if len(payload) > MAX_BYTES:
            raise ValueError("Input exceeds 16 MiB")
        payload = json.loads(payload)
        if args.command == "_worker":
            from contextlib import redirect_stdout

            with redirect_stdout(sys.stderr):
                output = worker(args.operation, payload)
        else:
            output = bounded_worker("evaluate", payload)
    elif args.command == "train":
        from .training_cli import train_manifest

        output = train_manifest(args.manifest)
    elif args.command == "ingest":
        from .learning import ingest_jsonl

        observations = ingest_jsonl(args.observations)
        usable = [o for o in observations if not o.synthetic and o.quality == "valid"]
        output = {
            "training_status": "insufficient_training_data",
            "rows": len(observations),
            "usable_rows": len(usable),
            "metrics": None,
            "reason": "Ingestion does not establish independent partitions or promotion",
        }
    else:
        output = benchmark()
        if args.check:
            if stable_report(output) != stable_report(json.loads(args.check.read_text())):
                raise SystemExit(
                    "Frozen benchmark differs; inspect inputs, versions and engine output"
                )
            print("Frozen benchmark reproduced (timing and RSS recorded separately)")
            return
        if args.output:
            args.output.write_text(json.dumps(output, indent=2, allow_nan=False) + "\n")
            print(args.output)
            return
    print(json.dumps(output, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
