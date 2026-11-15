#!/usr/bin/env python3
"""Build wheels and verify clean installs outside the checkout without source-path injection.

Requires uv, Python 3.12 and the consumer lock files. Installs dependencies into
isolated temporary environments; no service credentials, provider calls or training.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

PROJECTS = {
    "field-shift-contracts": "packages/field-shift-contracts",
    "field-shift-science": "packages/field-shift-science",
    "field-shift-api": "apps/api",
    "field-shift-ml": "packages/field-shift-ml",
    "field-shift-models": "packages/field-shift-models",
    "field-shift-rotation-model": "packages/field-shift-rotation-model",
    "field-shift-crop-runtime": "services/crop-model",
    "field-shift-agent": "services/agent",
}
MODULES = {
    "field-shift-contracts": ["field_shift_contracts.planning_v1"],
    "field-shift-science": ["field_shift_science.simulation.water_balance"],
    "field-shift-api": ["field_shift.api"],
    "field-shift-ml": ["field_shift_ml.cli", "field_shift_ml.train_moisture"],
    "field-shift-models": [
        "field_shift_models.provider",
        "field_shift_models.fixtures",
    ],
    "field-shift-crop-runtime": ["crop_runtime.server", "field_shift_models.provider"],
    "field-shift-agent": ["agent_service.api", "contracts", "integration_stub"],
    "field-shift-rotation-model": [
        "field_shift_rotation_model.runtime",
        "field_shift_rotation_model.server",
    ],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--python", default="3.12")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, output = args.root.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PYTHON", "AGENT_", "FIELD_SHIFT_", "POSTGRES_"))
        and k
        not in {
            "DATABASE_URL",
            "REDIS_URL",
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "ANTHROPIC_API_KEY",
        }
    }
    env["UV_NO_PROGRESS"] = "1"
    report = {"status": "failed", "checks": [], "projects": {}}

    def run(argv, cwd, capture=False):
        with (output / "commands.log").open("a") as log:
            log.write(
                json.dumps({"cwd": str(cwd), "argv": list(map(str, argv))}) + "\n"
            )
            log.flush()
            result = subprocess.run(
                list(map(str, argv)),
                cwd=cwd,
                env=env,
                text=True,
                stdout=subprocess.PIPE if capture else log,
                stderr=log,
                check=True,
            )
        return result.stdout

    try:
        with tempfile.TemporaryDirectory(prefix="field-shift-wheels-") as directory:
            temporary = Path(directory)
            wheels = temporary / "wheels"
            wheels.mkdir()
            scratch = temporary / "unrelated-working-directory"
            scratch.mkdir()
            env["HOME"] = str(temporary / "home")
            Path(env["HOME"]).mkdir()
            env["AGENT_DATA_DIR"] = str(temporary / "agent-data")
            for name, project in PROJECTS.items():
                run(
                    ["uv", "build", "--wheel", "--out-dir", wheels, root / project],
                    scratch,
                )
            owned = {}
            built = {}
            for wheel in wheels.glob("*.whl"):
                name = wheel.name.split("-")[0].replace("_", "-")
                built[name] = wheel
                with zipfile.ZipFile(wheel) as archive:
                    for member in archive.namelist():
                        if member.endswith("/") or ".dist-info/" in member:
                            continue
                        if member in owned:
                            raise RuntimeError(f"Wheel ownership collision: {member}")
                        if (
                            member.endswith((".env", ".sqlite"))
                            or "__pycache__" in member
                        ):
                            raise RuntimeError(
                                f"Runtime/private data in wheel: {member}"
                            )
                        owned[member] = name
            assert set(built) == set(PROJECTS)
            report["checks"].append(
                f"{len(PROJECTS)} wheels built; no overlapping files or runtime data"
            )
            for name, project in PROJECTS.items():
                print("Checking installed wheel:", name, flush=True)
                lock_project = (
                    "apps/api"
                    if name in ("field-shift-contracts", "field-shift-science")
                    else project
                )
                constraints = temporary / f"{name}.txt"
                run(
                    [
                        "uv",
                        "export",
                        "--project",
                        root / lock_project,
                        "--locked",
                        "--no-dev",
                        "--no-editable",
                        "--no-emit-local",
                        "--no-hashes",
                        "--output-file",
                        constraints,
                    ],
                    scratch,
                )
                environment = temporary / name
                run(["uv", "venv", "--python", args.python, environment], scratch)
                python = environment / "bin/python"
                # All first-party candidates come from the just-built wheels. The export
                # constrains third-party dependencies to the respective runtime lock.
                run(
                    [
                        "uv",
                        "pip",
                        "install",
                        "--python",
                        python,
                        "--constraint",
                        constraints,
                        "--find-links",
                        wheels,
                        built[name],
                    ],
                    scratch,
                )
                run(["uv", "pip", "check", "--python", python], scratch)
                code = """
import importlib, importlib.metadata, json, pathlib, sys
modules = json.loads(sys.argv[1])
prefix = pathlib.Path(sys.prefix).resolve()
for name in modules:
    module = importlib.import_module(name)
    assert pathlib.Path(module.__file__).resolve().is_relative_to(prefix), module.__file__
for distribution in importlib.metadata.distributions():
    direct = distribution.read_text('direct_url.json')
    assert not direct or not json.loads(direct).get('dir_info', {}).get('editable'), direct
print('Installed imports and noneditable metadata passed')
"""
                run([python, "-I", "-c", code, json.dumps(MODULES[name])], scratch)
                if name in ("field-shift-api", "field-shift-ml", "field-shift-science"):
                    run(
                        [
                            python,
                            "-I",
                            "-c",
                            """
from field_shift_science.geo_ml.moisture_model import get_moisture_model, get_moisture_model_v2
from field_shift_science.geo_ml.forecast_model import get_forecast_correction
from field_shift_science.simulation.root_zone_model import get_root_zone_correction
from field_shift_science.rotations.yield_model import get_rotation_yield_model
for read in (get_moisture_model, get_moisture_model_v2, get_forecast_correction,
             get_root_zone_correction, get_rotation_yield_model):
    assert read() is not None
""",
                        ],
                        scratch,
                    )
                if name == "field-shift-contracts":
                    run(
                        [
                            python,
                            "-I",
                            "-c",
                            """
from importlib.resources import files
from field_shift_contracts.planning_v1 import PlanningContracts
data = files('field_shift_contracts.planning_v1').joinpath('data/planning-contracts.json')
PlanningContracts.model_validate_json(data.read_text())
""",
                        ],
                        scratch,
                    )
                if name == "field-shift-models":
                    run(
                        [
                            python,
                            "-I",
                            "-c",
                            """
from field_shift_models.fixtures import synthetic_inputs
from field_shift_models.cli import bounded_worker
p,c,w,h = synthetic_inputs()
payload = dict(profile=p.model_dump(mode='json'), candidate=c.model_dump(mode='json'),
               weather=w.model_dump(mode='json'), hydraulics=vars(h))
result = bounded_worker('evaluate', payload)
assert result['result']['evaluation']['seasons'][0]['status'] == 'available'
""",
                        ],
                        scratch,
                    )
                    installed = run(
                        [
                            python,
                            "-I",
                            "-c",
                            "import importlib.metadata as m; print(m.version('numpy'))",
                        ],
                        scratch,
                        True,
                    )
                    assert installed.startswith("2."), installed
                if name == "field-shift-rotation-model":
                    run(
                        [
                            python,
                            "-I",
                            "-c",
                            """
import json
from importlib.resources import files
from field_shift_rotation_model import runtime
receipt = json.loads(files('field_shift_rotation_model').joinpath('upstream-receipt.json').read_text())
catalog = runtime.catalog()
assert catalog['source_commit'] == receipt['upstream_commit']
assert catalog['engine_version'] == runtime.installed_identity()
assert len(catalog['crops']) == 13
assert catalog['empirical_status'] == 'not_validated'
assert 'forecast' in catalog['operations']
assert files('field_shift_rotation_model').joinpath('examples/sutherland-2026-28/README.md').read_text()
""",
                        ],
                        scratch,
                    )
                if name == "field-shift-ml":
                    run([environment / "bin/field-shift-ml", "--help"], scratch)
                    run(
                        [
                            python,
                            "-I",
                            "-c",
                            """
import importlib, numpy
from field_shift_ml.cli import WORKFLOWS
for workflow in WORKFLOWS:
    importlib.import_module('field_shift_ml.' + workflow)
assert numpy.__version__ == '1.26.4'
""",
                        ],
                        scratch,
                    )
                if name in (
                    "field-shift-models",
                    "field-shift-crop-runtime",
                    "field-shift-ml",
                    "field-shift-rotation-model",
                ):
                    run(
                        [
                            python,
                            "-I",
                            "-c",
                            """
from importlib.util import find_spec
assert find_spec('field_shift') is None, 'The public API leaked into a private/offline environment'
""",
                        ],
                        scratch,
                    )
                report["projects"][name] = "passed"
            report["status"] = "passed"
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print("All installed-wheel checks passed", flush=True)


if __name__ == "__main__":
    main()
