"""Prevent silently skipped checks when change detection grows new outputs."""

import re
import shlex
import subprocess
import unittest
from pathlib import Path

from verify import build_plan


class CIPathChecks(unittest.TestCase):
    def test_fast_runner_includes_the_actual_ci_tooling_lint_and_format_scope(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/ci.yml").read_text()
        job = workflow.split("  agent_tooling:\n", 1)[1].split("  python_packages:\n", 1)[0]
        commands = dict(re.findall(r"^      - run: .* ruff (check|format) (.+)$", job, re.M))
        self.assertEqual(set(commands), {"check", "format"})
        plan = {check.name: check for check in build_plan(root)}
        for operation, arguments in commands.items():
            with self.subTest(operation=operation):
                expected = shlex.split(arguments)
                check = plan[f"development tooling {operation}"]
                actual_files = {
                    str((check.cwd / item).resolve().relative_to(root))
                    for item in check.argv
                    if item.endswith(".py")
                }
                self.assertEqual(actual_files, {item for item in expected if item.endswith(".py")})
                self.assertTrue(actual_files)
                self.assertEqual(
                    (check.cwd / check.argv[check.argv.index("--config") + 1]).resolve(),
                    root / expected[expected.index("--config") + 1],
                )
                if operation == "format":
                    self.assertIn("--check", check.argv)

    def test_shared_science_runs_paired_rotation_verification(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text()
        program = workflow.split("          awk '", 1)[1].split('\' "$RUNNER_TEMP/changes"', 1)[0]
        for path, expected in (
            (
                "packages/field-shift-science/src/field_shift_science/water/balance.py",
                "true",
            ),
            (
                "packages/field-shift-rotation-model/src/field_shift_rotation_model/engine.py",
                "true",
            ),
            ("docs/product/value-and-global-scenarios.md", "false"),
        ):
            with self.subTest(path=path):
                result = subprocess.run(
                    ["awk", program],
                    input=path + "\n",
                    text=True,
                    capture_output=True,
                    check=True,
                )
                flags = dict(line.split("=", 1) for line in result.stdout.splitlines())
                self.assertEqual(flags["rotation_model"], expected)

    def test_every_consumed_change_output_is_forwarded(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text()
        changes = workflow.split("  changes:\n", 1)[1].split("    steps:", 1)[0]
        output_names = re.findall(r"^      (\w+):", changes, re.MULTILINE)
        self.assertEqual(len(output_names), len(set(output_names)), "Duplicate CI output keys")
        forwarded = set(
            re.findall(
                r"^      (\w+): \$\{\{ steps.detect.outputs.\1 \}\}",
                changes,
                re.MULTILINE,
            )
        )
        consumed = set(re.findall(r"needs.changes.outputs.(\w+)", workflow))
        self.assertTrue(consumed)
        self.assertFalse(consumed - forwarded, f"Checks will silently skip: {consumed - forwarded}")

    def test_installed_package_check_is_required(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text()
        required = workflow.split("  required:\n", 1)[1]
        self.assertRegex(required, r"needs: \[[^\]]*python_packages")
        self.assertIn("python3 infra/verify_python_wheels.py", workflow)
