"""Deterministic exports using the repository's existing json-schema-to-typescript lock.

python -m field_shift_contracts.planning_v1.export --repo-root /path/to/checkout [--check]
Requires services/agent/node_modules (npm ci there) and node on PATH.
"""

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from . import PlanningContracts
from .acceptance import AcceptancePack, EmpiricalManifest, expand_recipe
from .common import DateWindow


def formatted(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def postprocess(node):
    """Same tuple/title adaptation as existing services/agent/contracts/export.py."""
    if isinstance(node, dict):
        node.pop("discriminator", None)
        for prop in node.get("properties", {}).values():
            prop.pop("title", None)
        if "prefixItems" in node:
            scalars = {"number": "number", "integer": "number", "string": "string"}
            node["tsType"] = "[" + ", ".join(scalars[i["type"]] for i in node["prefixItems"]) + "]"
        for item in node.values():
            postprocess(item)
    elif isinstance(node, list):
        for item in node:
            postprocess(item)


def schema(model) -> str:
    data = model.model_json_schema()
    postprocess(data)
    data["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    data["$comment"] = (
        "Generated from apps/api/src/field_shift/contracts/planning_v1. "
        "Python validators additionally enforce cross-field semantics; see README.md."
    )
    return formatted(data)


def outputs(root: Path) -> dict[Path, str]:
    ROOT = root.resolve()
    PACK = ROOT / "contracts/planning-v1"
    contract = schema(PlanningContracts)
    result = {
        PACK / "planning-v1.schema.json": contract,
        PACK / "cases.schema.json": schema(AcceptancePack),
        PACK / "empirical-sites.schema.json": schema(EmpiricalManifest),
    }
    cli = ROOT / "services/agent/node_modules/.bin/json2ts"
    with tempfile.TemporaryDirectory(prefix="planning-v1-") as temporary:
        input_path = Path(temporary) / "schema.json"
        input_path.write_text(contract)
        ts = subprocess.run(
            [
                str(cli),
                "-i",
                str(input_path),
                "--additionalProperties",
                "false",
                "--ignoreMinAndMaxItems",
                "--bannerComment",
                "/* Generated from planning-v1.schema.json; "
                "run planning_v1.export. Do not edit. */",
            ],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
    result[ROOT / "apps/web/src/api/planningV1.generated.ts"] = ts
    example_path = PACK / "examples/planning-contracts.json"
    example = PlanningContracts.model_validate_json(example_path.read_text())
    roundtrip = example.model_dump(mode="json")
    result[PACK / "examples/roundtrip.generated.ts"] = (
        "// Generated compile-time check; no type assertion or runtime fallback.\n"
        'import type { PlanningContracts } from "../../../apps/web/src/api/planningV1.generated";\n'
        "export const example = "
        + json.dumps(roundtrip, indent=2)
        + " satisfies PlanningContracts;\n"
    )
    cases = AcceptancePack.model_validate_json((PACK / "cases.v1.json").read_text())
    expanded = {}
    for name, recipe in cases.weather_profiles.items():
        if recipe.window:
            expanded[name] = [d.model_dump(mode="json") for d in expand_recipe(recipe)]
    for case in cases.cases:
        weather = case.inputs.get("weather")
        window = case.inputs.get("window")
        if weather in cases.weather_profiles and window:
            bounds = DateWindow.model_validate_json(
                json.dumps({"start": window[0], "end": window[1]})
            )
            expanded[case.id] = [
                d.model_dump(mode="json")
                for d in expand_recipe(cases.weather_profiles[weather], bounds)
            ]
    result[PACK / "weather-fixtures.generated.json"] = formatted(
        {
            "kind": "synthetic_contract",
            "not_empirical_evidence": True,
            "dates_are_inclusive": True,
            "records": expanded,
        }
    )
    result[PACK / "export-manifest.json"] = formatted(
        {
            "contract_version": "1.0.0",
            "generator": "pydantic + json-schema-to-typescript (services/agent/package-lock.json)",
            "sha256": {
                str(p.relative_to(ROOT)): hashlib.sha256(t.encode()).hexdigest()
                for p, t in result.items()
            },
        }
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--repo-root",
        type=Path,
        required=True,
        help="Explicit checkout to check or regenerate",
    )
    args = parser.parse_args()
    ROOT = args.repo_root.resolve()
    stale = []
    for path, text in outputs(ROOT).items():
        if args.check:
            if not path.exists() or path.read_text() != text:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            print(path.relative_to(ROOT))
    canonical = ROOT / "contracts/planning-v1/examples/planning-contracts.json"
    bundled = (
        ROOT
        / "packages/field-shift-contracts/src/field_shift_contracts"
        / "planning_v1/data/planning-contracts.json"
    )
    if args.check:
        if bundled.read_bytes() != canonical.read_bytes():
            stale.append(str(bundled.relative_to(ROOT)))
    else:
        bundled.write_bytes(canonical.read_bytes())
    if stale:
        raise SystemExit("Stale generated contracts: " + ", ".join(stale))
    if args.check:
        print("Planning-v1 generated files are current")


if __name__ == "__main__":
    main()
