"""Run ordered local checks without installing dependencies or changing test selection silently."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Check:
    name: str
    cwd: Path
    argv: tuple[str, ...]


def build_plan(root: Path, mode: str = "fast", build: bool = False) -> list[Check]:
    """Return commands in dependency order; all paths are relative to this checkout."""
    root = root.resolve()
    if mode == "tooling":
        if build:
            raise ValueError("--build applies only to --mode fast")
        return [
            Check(
                "agent tree configuration",
                root,
                (sys.executable, "infra/check_agent_tree.py"),
            ),
            Check(
                "tooling unit tests",
                root,
                (sys.executable, "-m", "unittest", "discover", "-s", "infra", "-p", "test_*.py"),
            ),
            Check(
                "Python package boundaries",
                root,
                (sys.executable, "infra/check_python_packaging.py"),
            ),
        ]
    if mode != "fast":
        raise ValueError(f"Unknown verification mode: {mode}")

    checks: list[Check] = []

    def add(name: str, directory: str, *argv: str) -> None:
        checks.append(Check(name, root / directory, argv))

    add("widget contract freshness", "services/agent/frontend", "npm", "run", "check:validators")
    add("agent contract TypeScript", "services/agent", "npm", "run", "check:ts")
    add("Python package boundaries", ".", sys.executable, "infra/check_python_packaging.py")
    for operation in ("check", "format"):
        add(
            f"development tooling {operation}",
            "apps/api",
            "uv",
            "run",
            "--no-sync",
            "ruff",
            operation,
            *(("--check",) if operation == "format" else ()),
            "--config",
            "pyproject.toml",
            "../../infra/check_agent_tree.py",
            "../../infra/test_agent_tree.py",
            "../../infra/test_ci_paths.py",
            "../../infra/verify_cpu_benchmark.py",
            "../../infra/test_cpu_benchmark.py",
            "../../infra/verify.py",
            "../../infra/test_verify.py",
        )
    for operation in ("check", "format"):
        args = ("--check",) if operation == "format" else ()
        add(
            f"shared Python packages {operation}",
            "apps/api",
            "uv",
            "run",
            "--no-sync",
            "ruff",
            operation,
            *args,
            "../../packages/field-shift-contracts/src",
            "../../packages/field-shift-science/src",
        )
    add(
        "planning contract freshness",
        "apps/api",
        "uv",
        "run",
        "--no-sync",
        "python",
        "-m",
        "field_shift_contracts.planning_v1.export",
        "--repo-root",
        str(root),
        "--check",
    )
    add("planning JSON contracts", ".", "node", "contracts/planning-v1/check.mjs")
    add(
        "planning contract TypeScript",
        ".",
        "node",
        "apps/web/node_modules/typescript/bin/tsc",
        "-p",
        "contracts/planning-v1/tsconfig.json",
    )
    add(
        "private crop runtime lint",
        "apps/api",
        "uv",
        "run",
        "--no-sync",
        "ruff",
        "check",
        "--config",
        "pyproject.toml",
        "../../services/crop-model/crop_runtime",
    )
    add(
        "private crop runtime format",
        "apps/api",
        "uv",
        "run",
        "--no-sync",
        "ruff",
        "format",
        "--check",
        "--config",
        "pyproject.toml",
        "../../services/crop-model/crop_runtime",
    )
    if build:
        add("web production build", "apps/web", "npm", "run", "build")
        add("widget production build", "services/agent/frontend", "npm", "run", "build")
    add("web TypeScript", "apps/web", "npm", "run", "typecheck")
    add("widget TypeScript", "services/agent/frontend", "npm", "run", "typecheck")

    python_packages = (
        ("API", "apps/api"),
        ("ML", "packages/field-shift-ml"),
        ("models", "packages/field-shift-models"),
        ("rotation model", "packages/field-shift-rotation-model"),
    )
    for name, directory in python_packages:
        add(f"{name} lint", directory, "uv", "run", "--no-sync", "ruff", "check", ".")
        add(f"{name} format", directory, "uv", "run", "--no-sync", "ruff", "format", "--check", ".")
    add(
        "API unit tests",
        "apps/api",
        "uv",
        "run",
        "--no-sync",
        "pytest",
        "-q",
        "-m",
        "not integration",
    )
    for name, directory in (*python_packages[1:], ("agent", "services/agent")):
        add(f"{name} unit tests", directory, "uv", "run", "--no-sync", "pytest", "-q")
    add("web unit tests", "apps/web", "npm", "test")
    add("widget unit tests", "services/agent/frontend", "npm", "test")
    return checks


def execute(checks: Sequence[Check], *, list_only: bool = False) -> int:
    """Run checks sequentially, stopping immediately at the first unsuccessful command."""
    for index, check in enumerate(checks, start=1):
        print(f"[{index}/{len(checks)}] {check.name}", flush=True)
        print(f"cwd={json.dumps(str(check.cwd))} argv={json.dumps(check.argv)}", flush=True)
        if list_only:
            continue
        try:
            result = subprocess.run(check.argv, cwd=check.cwd, check=False)
        except FileNotFoundError as error:
            print(f"FAILED: executable or working directory missing: {error}", file=sys.stderr)
            return 127
        except OSError as error:
            print(f"FAILED: cannot execute {check.name}: {error}", file=sys.stderr)
            return 126
        if result.returncode:
            print(f"FAILED: {check.name} exited {result.returncode}", file=sys.stderr)
            return result.returncode if result.returncode > 0 else 128 - result.returncode
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fast", "tooling"), default="fast")
    parser.add_argument(
        "--build", action="store_true", help="include web/widget builds in fast mode"
    )
    parser.add_argument("--list", action="store_true", help="print commands without executing them")
    args = parser.parse_args(argv)
    if args.build and args.mode != "fast":
        parser.error("--build applies only to --mode fast")
    print(
        "Scope: local fast checks. PostgreSQL/Redis integration, browser E2E, Docker builds, "
        "restore checks and optional/live suites are not covered."
        if args.mode == "fast"
        else "Scope: agent tooling validation and unit tests; application suites are not run.",
        flush=True,
    )
    result = execute(
        build_plan(Path(__file__).resolve().parents[1], args.mode, args.build),
        list_only=args.list,
    )
    if result == 0:
        print(
            "Plan listed; no checks executed." if args.list else "All planned commands succeeded."
        )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
