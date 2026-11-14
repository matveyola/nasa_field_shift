"""Prevent silently skipped checks when change detection grows new outputs."""

import re
import unittest
from pathlib import Path


class CIPathChecks(unittest.TestCase):
    def test_every_consumed_change_output_is_forwarded(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text()
        changes = workflow.split("  changes:\n", 1)[1].split("    steps:", 1)[0]
        output_names = re.findall(r"^      (\w+):", changes, re.M)
        self.assertEqual(len(output_names), len(set(output_names)), "Duplicate CI output keys")
        forwarded = set(
            re.findall(r"^      (\w+): \$\{\{ steps.detect.outputs.\1 \}\}", changes, re.M)
        )
        consumed = set(re.findall(r"needs.changes.outputs.(\w+)", workflow))
        self.assertTrue(consumed)
        self.assertFalse(consumed - forwarded, f"Checks will silently skip: {consumed - forwarded}")

    def test_installed_package_check_is_required(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text()
        required = workflow.split("  required:\n", 1)[1]
        self.assertRegex(required, r"needs: \[[^\]]*python_packages")
        self.assertIn("python3 infra/verify_python_wheels.py", workflow)
