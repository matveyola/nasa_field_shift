"""Exercise packaging policy on disposable projects, independent of the current migration."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from check_python_packaging import validate

PROJECTS = {
    "apps/api": ("field-shift-api", "field_shift"),
    "packages/field-shift-contracts": ("field-shift-contracts", "field_shift_contracts"),
    "packages/field-shift-science": ("field-shift-science", "field_shift_science"),
    "packages/field-shift-ml": ("field-shift-ml", "field_shift_ml"),
    "packages/field-shift-models": ("field-shift-models", "field_shift_models"),
    "services/crop-model": ("field-shift-crop-model", "crop_runtime"),
    "services/agent": ("field-shift-agent", "agent_service"),
}


class PythonPackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="packaging policy ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory, (name, module) in PROJECTS.items():
            self.write(f"{directory}/pyproject.toml", self.manifest(name))
            self.write(f"{directory}/src/{module}/__init__.py", "")

    @staticmethod
    def manifest(name, dependencies="[]", extra=""):
        return (
            f'[build-system]\nrequires = ["setuptools>=75"]\n'
            f'build-backend = "setuptools.build_meta"\n'
            f'[project]\nname = "{name}"\nversion = "1.0"\n'
            f"dependencies = {dependencies}\n{extra}"
        )

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def assertViolation(self, expected):
        errors = validate(self.root)
        self.assertTrue(any(expected in error for error in errors), errors)

    def test_valid_packages_with_regular_and_lazy_declared_imports(self):
        self.write(
            "apps/api/pyproject.toml",
            self.manifest(
                "field-shift-api", '["field-shift-contracts>=1", "field_shift_science[geo]>=1"]'
            ),
        )
        self.write(
            "apps/api/src/field_shift/main.py",
            """
import field_shift_contracts
from . import internal
def calculate():
    from field_shift_science.water import reserve
    return reserve
""",
        )
        self.assertEqual(validate(self.root), [])

    def test_missing_backend_in_each_project(self):
        for relative, (name, _) in PROJECTS.items():
            with self.subTest(project=relative):
                path = self.root / relative / "pyproject.toml"
                path.write_text(f'[project]\nname = "{name}"\n')
                self.assertViolation(f"{relative}/pyproject.toml: explicit build-backend")
                path.write_text(self.manifest(name))

    def test_missing_and_invalid_manifests_fail(self):
        path = self.root / "services/crop-model/pyproject.toml"
        path.unlink()
        self.assertViolation("services/crop-model/pyproject.toml")
        path.write_text("[broken")
        self.assertViolation("services/crop-model/pyproject.toml")

    def test_dependency_is_required_for_import_inside_function(self):
        self.write(
            "packages/field-shift-models/src/field_shift_models/model.py",
            """
def evaluate():
    from field_shift_contracts.planning import Request
    return Request
""",
        )
        self.assertViolation(
            "model.py:3: import field_shift_contracts.planning requires "
            "dependency field-shift-contracts"
        )

    def test_optional_dependency_covers_optional_import(self):
        self.write(
            "apps/api/pyproject.toml",
            self.manifest(
                "field-shift-api",
                extra="[project.optional-dependencies]\n"
                "science = [\"field-shift-science>=1; python_version >= '3.11'\"]\n",
            ),
        )
        self.write(
            "apps/api/src/field_shift/plugin.py",
            """
try:
    import field_shift_science
except ImportError:
    pass
""",
        )
        self.assertEqual(validate(self.root), [])

    def test_dev_group_does_not_supply_production_dependency(self):
        self.write(
            "apps/api/pyproject.toml",
            self.manifest(
                "field-shift-api", extra='[dependency-groups]\ndev = ["field-shift-science"]\n'
            ),
        )
        self.write("apps/api/src/field_shift/main.py", "import field_shift_science\n")
        self.assertViolation("requires dependency field-shift-science")

    def test_flat_agent_contracts_are_owned_by_agent_not_planning_contracts(self):
        self.write(
            "services/agent/agent_service/main.py",
            "from contracts import Chat\nfrom integration_stub import service\n",
        )
        self.write("services/agent/evals/evaluate.py", "import agent_service\n")
        self.assertEqual(validate(self.root), [])
        self.write("services/agent/agent_service/main.py", "import field_shift_contracts\n")
        self.assertViolation("requires dependency field-shift-contracts")

    def test_dynamic_dependency_file_is_resolved_relative_to_project(self):
        self.write(
            "packages/field-shift-ml/pyproject.toml",
            self.manifest(
                "field-shift-ml",
                extra='[tool.setuptools.dynamic]\ndependencies = {file = ["requirements.txt"]}\n',
            ),
        )
        self.write(
            "packages/field-shift-ml/requirements.txt", "# Runtime\nfield-shift-science>=1\n"
        )
        self.write(
            "packages/field-shift-ml/src/field_shift_ml/main.py", "import field_shift_science\n"
        )
        self.assertEqual(validate(self.root), [])

    def test_pytest_path_configuration_is_rejected(self):
        self.write(
            "apps/api/pyproject.toml",
            self.manifest(
                "field-shift-api", extra='[tool.pytest.ini_options]\npythonpath = ["src"]\n'
            ),
        )
        self.assertViolation("pytest pythonpath is forbidden")
        self.write("apps/api/pyproject.toml", self.manifest("field-shift-api"))
        for name, section in [("pytest.ini", "pytest"), ("setup.cfg", "tool:pytest")]:
            with self.subTest(name=name):
                path = self.write(f"apps/api/{name}", f"[{section}]\npythonpath = src\n")
                self.assertViolation(f"apps/api/{name}: pytest pythonpath")
                path.unlink()

    def test_python_path_mutations_and_import_aliases_are_rejected(self):
        cases = [
            "import sys\nsys.path.insert(0, 'src')",
            "import sys as runtime\nruntime.path += ['src']",
            "from sys import path as search\nsearch.append('src')",
            "import sys\nsys.path[:] = ['src']",
            "from site import addsitedir as add\nadd('src')",
            "import site\nsite.addsitedir('src')",
        ]
        for code in cases:
            with self.subTest(code=code):
                self.write("infra/runner.py", code)
                self.assertViolation("mutation is forbidden")

    def test_python_environment_overrides_are_rejected(self):
        cases = [
            "import os\nos.environ['PYTHONPATH'] = 'src'",
            "env = {'PYTHONPATH': 'src'}",
            "env = dict(PYTHONHOME='/python')",
            "import os\nos.environ.update(PYTHONPATH='src')",
            "import os\nos.putenv('PYTHONPATH', 'src')",
            "def fixture(monkeypatch):\n    monkeypatch.setenv('PYTHONPATH', 'apps/api/src')",
            "def fixture(monkeypatch):\n    monkeypatch.setenv('PYTHONHOME', '/python')",
            "import subprocess\nsubprocess.run(['env', 'PYTHONPATH=src', 'python'])",
        ]
        for code in cases:
            with self.subTest(code=code):
                self.write("infra/runner.py", code)
                self.assertViolation("environment")

    def test_environment_cleanup_and_path_reads_are_allowed(self):
        self.write("infra/clean.sh", "unset PYTHONPATH PYTHONHOME\n")
        self.write(
            "infra/runner.py",
            """
'''Do not start Python with PYTHONPATH=src.'''
import os
import sys
print(sys.path)
os.environ.pop('PYTHONPATH', None)
os.unsetenv('PYTHONHOME')
def clean(monkeypatch):
    monkeypatch.delenv('PYTHONPATH', raising=False)
    monkeypatch.setenv('FIELD_SHIFT_ACCESS_MODE', 'team')
""",
        )
        self.assertEqual(validate(self.root), [])

    def test_shipped_shell_docker_ci_and_npm_overrides_are_rejected(self):
        cases = {
            "infra/run.sh": "export PYTHONPATH=src\npython app.py\n",
            "apps/api/Dockerfile": "FROM python:3.12\nENV PYTHONPATH /app/src\n",
            ".github/workflows/test.yml": "steps:\n  - run: PYTHONPATH=src pytest\n",
            ".github/workflows/env.yaml": "env:\n  PYTHONPATH: src\n",
            ".github/workflows/quoted.yaml": 'env:\n  "PYTHONPATH": src\n',
            "apps/web/package.json": '{"scripts":{"test":"PYTHONPATH=../api/src pytest"}}',
        }
        for relative, content in cases.items():
            with self.subTest(path=relative):
                path = self.write(relative, content)
                self.assertViolation(relative)
                path.unlink()

    def test_documentation_tests_caches_and_tool_imports_are_not_dependencies(self):
        for directory in [".venv", "node_modules", "dist"]:
            self.write(f"apps/api/{directory}/example.py", "import sys\nsys.path.append('src')")
        self.write("apps/api/tests/test_example.py", "import field_shift_ml")
        self.write("apps/api/src/field_shift/tests/helpers.py", "import field_shift_ml")
        self.write("docs/setup.md", "PYTHONPATH=src pytest")
        self.write("apps/api/src/tools/example.py", "import field_shift_ml")
        self.write("apps/api/package.json", '{"description":"PYTHONPATH=src","scripts":{}}')
        self.assertEqual(validate(self.root), [])

    def test_conftest_and_test_helpers_cannot_mutate_import_paths(self):
        cases = {
            "apps/api/tests/conftest.py": "import sys\nsys.path.insert(0, '../src')",
            "services/agent/tests/helpers.py": "from site import addsitedir\naddsitedir('..')",
            "infra/test_helper.py": "import os\nos.environ['PYTHONPATH'] = 'apps/api/src'",
            "packages/field-shift-models/tests/test_worker.py": (
                "def test_worker(monkeypatch):\n"
                "    monkeypatch.setenv('PYTHONPATH', 'apps/api/src')"
            ),
            "packages/field-shift-ml/tests/test_imports.py": "import sys\nsys.path += ['src']",
        }
        for relative, content in cases.items():
            with self.subTest(path=relative):
                path = self.write(relative, content)
                self.assertViolation(relative)
                path.unlink()

    def test_only_guard_negative_fixture_file_is_exempt(self):
        self.write("infra/test_python_packaging.py", "example = 'PYTHONPATH=src pytest'")
        self.write(
            "apps/api/tests/conftest.py",
            """
'''Do not set PYTHONPATH=src to make imports pass.'''
def clean(monkeypatch):
    monkeypatch.delenv('PYTHONPATH', raising=False)
""",
        )
        self.assertEqual(validate(self.root), [])
        self.write("apps/api/tests/test_python_packaging.py", "import sys\nsys.path.append('src')")
        self.assertViolation("apps/api/tests/test_python_packaging.py")

    def test_syntax_errors_are_failures_instead_of_silently_skipped(self):
        self.write("apps/api/src/field_shift/main.py", "not valid python !")
        self.assertViolation("cannot inspect Python")

    def test_cli_accepts_an_explicit_root_from_an_unrelated_working_directory(self):
        script = Path(__file__).with_name("check_python_packaging.py")
        result = subprocess.run(
            [sys.executable, str(script), "--root", str(self.root)],
            cwd="/",
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS", result.stdout)
        self.write("apps/api/src/field_shift/bad.py", "import field_shift_ml")
        result = subprocess.run(
            [sys.executable, str(script), "--root", str(self.root)],
            cwd="/",
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("requires dependency field-shift-ml", result.stderr)


if __name__ == "__main__":
    unittest.main()
