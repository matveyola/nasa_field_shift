"""Check declared Python package boundaries and reject import-path workarounds.

This static check covers ordinary imports (including lazy imports), not arbitrary
computed import names or executable code assembled at runtime. Wheel contents and
clean-environment installation remain separate integration checks.
"""

from __future__ import annotations

import argparse
import ast
import configparser
import json
import re
import sys
import tomllib
from pathlib import Path

PROJECTS = {
    "apps/api": ("field_shift",),
    "packages/field-shift-contracts": ("field_shift_contracts",),
    "packages/field-shift-science": ("field_shift_science",),
    "packages/field-shift-ml": ("field_shift_ml",),
    "packages/field-shift-models": ("field_shift_models",),
    "packages/field-shift-rotation-model": ("field_shift_rotation_model",),
    "services/crop-model": ("crop_runtime",),
    "services/agent": ("agent_service", "contracts", "integration_stub", "evals"),
}
EXCLUDED = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
    "test-results",
    "playwright-report",
    ".cache",
}
PATH_VARIABLES = {"PYTHONPATH", "PYTHONHOME"}
ENV_ASSIGNMENT = re.compile(r"\b(?:PYTHONPATH|PYTHONHOME)[\"']?\s*[=:]")
DOCKER_ENV = re.compile(r"\bENV\s+(?:PYTHONPATH|PYTHONHOME)\s+\S")


def files(directory: Path, *, include_tests: bool = False):
    """Prune environments/output; dependency checks can additionally exclude tests."""
    if not directory.is_dir():
        return
    for entry in sorted(directory.iterdir()):
        if entry.is_symlink() or entry.name in EXCLUDED:
            continue
        if not include_tests and (
            entry.name == "tests" or entry.name.startswith("test_")
        ):
            continue
        if entry.is_dir():
            yield from files(entry, include_tests=include_tests)
        elif entry.is_file():
            yield entry


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def qualified(node: ast.AST, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return qualified(node.value, aliases) + "." + node.attr
    if isinstance(node, ast.Subscript):
        return qualified(node.value, aliases)
    return ""


def python_violations(tree: ast.AST) -> list[tuple[int, str]]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for value in node.names:
                aliases[value.asname or value.name.split(".")[0]] = value.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for value in node.names:
                aliases[value.asname or value.name] = node.module + "." + value.name
    findings: set[tuple[int, str]] = set()
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }

    def flag(node, message):
        findings.add((node.lineno, message))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)):
            targets = (
                node.targets
                if isinstance(node, (ast.Assign, ast.Delete))
                else [node.target]
            )
            for target in targets:
                if qualified(target, aliases) == "sys.path":
                    flag(node, "sys.path mutation is forbidden")
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value in PATH_VARIABLES
                    and not isinstance(node, ast.Delete)
                ):
                    flag(node, "import-path environment override is forbidden")
        if isinstance(node, ast.Call):
            name = qualified(node.func, aliases)
            if name == "site.addsitedir" or name in {
                f"sys.path.{method}"
                for method in (
                    "append",
                    "extend",
                    "insert",
                    "remove",
                    "pop",
                    "clear",
                    "sort",
                    "reverse",
                )
            }:
                flag(node, "import-path mutation is forbidden")
            if (
                (
                    name
                    in {
                        "os.putenv",
                        "os.environ.__setitem__",
                        "os.environ.setdefault",
                    }
                    or name.endswith(".setenv")
                )
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in PATH_VARIABLES
            ):
                flag(node, "import-path environment override is forbidden")
            if any(keyword.arg in PATH_VARIABLES for keyword in node.keywords):
                flag(node, "import-path environment override is forbidden")
        if isinstance(node, ast.Dict) and any(
            isinstance(key, ast.Constant) and key.value in PATH_VARIABLES
            for key in node.keys
        ):
            flag(node, "import-path environment override is forbidden")
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and re.search(r"\b(?:PYTHONPATH|PYTHONHOME)=", node.value)
        ):
            # Catch literal subprocess commands/argv assignments without interpreting docstrings.
            flag(node, "literal import-path environment assignment is forbidden")
    return sorted(findings)


def dependencies(
    root: Path, directory: Path, config: dict, errors: list[str]
) -> set[str]:
    project = config.get("project", {})
    requirements = list(project.get("dependencies", []))
    for values in project.get("optional-dependencies", {}).values():
        requirements.extend(values)
    dynamic = config.get("tool", {}).get("setuptools", {}).get("dynamic", {})
    dependency_files = dynamic.get("dependencies", {}).get("file", [])
    if isinstance(dependency_files, str):
        dependency_files = [dependency_files]
    for filename in dependency_files:
        path = (directory / filename).resolve()
        if not path.is_relative_to(directory.resolve()):
            errors.append(
                f"{directory.relative_to(root)}: dependency file escapes project"
            )
            continue
        try:
            requirements.extend(
                line.strip()
                for line in path.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            )
        except OSError as exc:
            errors.append(
                f"{directory.relative_to(root)}: cannot read dependencies: {exc}"
            )
    return {
        normalize(match.group())
        for value in requirements
        if isinstance(value, str) and (match := re.match(r"[A-Za-z0-9_.-]+", value))
    }


def validate(root: Path) -> list[str]:
    root = root.resolve()
    errors: list[str] = []
    configs: dict[str, dict] = {}
    owners: dict[str, tuple[str, str]] = {}
    for relative, modules in PROJECTS.items():
        manifest = root / relative / "pyproject.toml"
        try:
            config = tomllib.loads(manifest.read_text())
        except (OSError, ValueError) as exc:
            errors.append(f"{relative}/pyproject.toml: {exc}")
            continue
        configs[relative] = config
        backend = config.get("build-system", {}).get("build-backend")
        if not isinstance(backend, str) or not backend.strip():
            errors.append(f"{relative}/pyproject.toml: explicit build-backend required")
        name = config.get("project", {}).get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{relative}/pyproject.toml: project.name required")
            continue
        for module in modules:
            owners[module] = (relative, normalize(name))
        if "pythonpath" in config.get("tool", {}).get("pytest", {}).get(
            "ini_options", {}
        ):
            errors.append(f"{relative}/pyproject.toml: pytest pythonpath is forbidden")

    for relative, config in configs.items():
        directory = root / relative
        declared = dependencies(root, directory, config, errors)
        source_dirs = [directory / "src"]
        source_dirs.extend(directory / module for module in PROJECTS[relative])
        for source in source_dirs:
            for path in files(source):
                if path.suffix != ".py" or "tools" in path.relative_to(source).parts:
                    continue
                try:
                    tree = ast.parse(path.read_text(), filename=str(path))
                except (OSError, SyntaxError) as exc:
                    errors.append(
                        f"{path.relative_to(root)}: cannot inspect Python: {exc}"
                    )
                    continue
                for node in ast.walk(tree):
                    imported = []
                    if isinstance(node, ast.Import):
                        imported = [name.name for name in node.names]
                    elif (
                        isinstance(node, ast.ImportFrom)
                        and node.level == 0
                        and node.module
                    ):
                        imported = [node.module]
                    for name in imported:
                        owner = owners.get(name.split(".")[0])
                        if owner and owner[0] != relative and owner[1] not in declared:
                            errors.append(
                                f"{path.relative_to(root)}:{node.lineno}: "
                                f"import {name} requires dependency {owner[1]}"
                            )

    for base in ("apps", "packages", "services", "infra", ".github/workflows"):
        for path in files(root / base, include_tests=True):
            relative = path.relative_to(root)
            # These controlled negative examples intentionally contain prohibited commands.
            # Other test modules, conftests and helpers must obey the path policy.
            if relative.as_posix() == "infra/test_python_packaging.py":
                continue
            if path.suffix == ".py":
                try:
                    tree = ast.parse(path.read_text(), filename=str(path))
                    errors.extend(
                        f"{relative}:{line}: {message}"
                        for line, message in python_violations(tree)
                    )
                except (OSError, SyntaxError) as exc:
                    errors.append(f"{relative}: cannot inspect Python: {exc}")
            elif path.name == "package.json":
                try:
                    scripts = json.loads(path.read_text()).get("scripts", {})
                    for name, command in scripts.items():
                        if ENV_ASSIGNMENT.search(command):
                            errors.append(
                                f"{relative}: script {name} overrides import path"
                            )
                except (OSError, ValueError) as exc:
                    errors.append(f"{relative}: cannot inspect npm scripts: {exc}")
            elif (
                path.suffix in {".sh", ".bash"}
                or path.name.startswith("Dockerfile")
                or base == ".github/workflows"
            ):
                for line, content in enumerate(path.read_text().splitlines(), 1):
                    if not content.lstrip().startswith("#") and (
                        ENV_ASSIGNMENT.search(content)
                        or (
                            path.name.startswith("Dockerfile")
                            and DOCKER_ENV.search(content)
                        )
                    ):
                        errors.append(
                            f"{relative}:{line}: import-path environment override"
                        )
            elif path.name in {"pytest.ini", "setup.cfg", "tox.ini"}:
                config = configparser.ConfigParser()
                try:
                    config.read_string(path.read_text())
                    for section in ("pytest", "tool:pytest", "testenv"):
                        if config.has_option(section, "pythonpath"):
                            errors.append(f"{relative}: pytest pythonpath is forbidden")
                except configparser.Error as exc:
                    errors.append(f"{relative}: invalid configuration: {exc}")
    return sorted(set(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args(argv)
    errors = validate(args.root)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("Python build backends, first-party dependencies and import paths: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
