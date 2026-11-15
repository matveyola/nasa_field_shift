"""Exercise the runner using isolated subprocesses; never invoke application suites."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import verify


class VerifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.output = io.StringIO()
        self.errors = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.enterContext(contextlib.redirect_stderr(self.errors))

    def test_execution_uses_assigned_checkout_and_finishes_in_order(self) -> None:
        worker = self.root / "checkout with spaces"
        worker.mkdir()
        checks = [
            verify.Check(
                "first",
                worker,
                (
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('order').write_text(str(Path.cwd()))",
                ),
            ),
            verify.Check(
                "second",
                worker,
                (
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path; "
                        "assert Path('order').read_text() == str(Path.cwd()); "
                        "Path('order').write_text('complete')"
                    ),
                ),
            ),
        ]
        self.assertEqual(verify.execute(checks), 0)
        self.assertEqual((worker / "order").read_text(), "complete")
        self.assertIn(json.dumps(str(worker)), self.output.getvalue())
        self.assertIn(json.dumps(checks[0].argv), self.output.getvalue())

    def test_failure_stops_before_later_command(self) -> None:
        checks = [
            verify.Check(
                "failure", self.root, (sys.executable, "-c", "raise SystemExit(7)")
            ),
            verify.Check(
                "must not run",
                self.root,
                (
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('unexpected').touch()",
                ),
            ),
        ]
        self.assertEqual(verify.execute(checks), 7)
        self.assertFalse((self.root / "unexpected").exists())
        self.assertIn("exited 7", self.errors.getvalue())

    def test_missing_executable_fails_and_stops(self) -> None:
        checks = [
            verify.Check("missing", self.root, (str(self.root / "missing-command"),)),
            verify.Check("later", self.root, (sys.executable, "-c", "pass")),
        ]
        with patch.object(verify.subprocess, "run", wraps=subprocess.run) as run:
            self.assertEqual(verify.execute(checks), 127)
            self.assertEqual(run.call_count, 1)

    def test_list_prints_actual_commands_without_execution(self) -> None:
        with patch.object(verify.subprocess, "run") as run:
            self.assertEqual(verify.main(["--mode", "fast", "--build", "--list"]), 0)
        run.assert_not_called()
        self.assertIn('["npm", "run", "build"]', self.output.getvalue())
        self.assertIn("no checks executed", self.output.getvalue())

    def test_contracts_and_builds_precede_consumers(self) -> None:
        plan = verify.build_plan(self.root, build=True)
        self.assertEqual(plan[0].argv, ("npm", "run", "check:validators"))
        self.assertEqual(plan[1].argv, ("npm", "run", "check:ts"))
        first_unit = next(
            index for index, check in enumerate(plan) if "unit tests" in check.name
        )
        builds = [
            index
            for index, check in enumerate(plan)
            if check.argv == ("npm", "run", "build")
        ]
        self.assertEqual(len(builds), 2)
        self.assertTrue(all(1 < index < first_unit for index in builds))
        self.assertTrue(all(check.cwd.is_absolute() for check in plan))

    def test_planning_contracts_precede_consumer_builds(self) -> None:
        plan = verify.build_plan(self.root, build=True)
        names = [check.name for check in plan]
        for required in [
            "planning contract freshness",
            "planning JSON contracts",
            "planning contract TypeScript",
        ]:
            self.assertIn(required, names)
            self.assertLess(names.index(required), names.index("web production build"))
        self.assertIn("private crop runtime lint", names)
        self.assertIn("private crop runtime format", names)

    def test_fast_covers_all_unit_environments_without_implicit_sync(self) -> None:
        plan = verify.build_plan(self.root)
        unit = {
            check.cwd.relative_to(self.root).as_posix(): check.argv
            for check in plan
            if "unit tests" in check.name
        }
        self.assertEqual(
            set(unit),
            {
                "apps/api",
                "packages/field-shift-ml",
                "packages/field-shift-models",
                "packages/field-shift-rotation-model",
                "services/agent",
                "apps/web",
                "services/agent/frontend",
            },
        )
        self.assertEqual(unit["apps/api"][-2:], ("-m", "not integration"))
        for check in plan:
            if check.argv[0] == "uv":
                self.assertEqual(check.argv[1:3], ("run", "--no-sync"))
        self.assertFalse(any(check.argv == ("npm", "run", "build") for check in plan))

    def test_tooling_validates_configuration_before_unit_tests(self) -> None:
        plan = verify.build_plan(self.root, mode="tooling")
        self.assertEqual(plan[0].argv, (sys.executable, "infra/check_agent_tree.py"))
        self.assertEqual(plan[1].argv[1:4], ("-m", "unittest", "discover"))
        self.assertTrue(all(check.cwd == self.root for check in plan))

    def test_tooling_rejects_build_instead_of_ignoring_it(self) -> None:
        with (
            patch.object(verify.subprocess, "run") as run,
            self.assertRaises(SystemExit) as error,
        ):
            verify.main(["--mode", "tooling", "--build"])
        self.assertEqual(error.exception.code, 2)
        run.assert_not_called()

    def test_signal_failure_is_nonzero_and_stops(self) -> None:
        checks = [verify.Check("terminated", self.root, ("fake-command",))] * 2
        with patch.object(
            verify.subprocess, "run", return_value=subprocess.CompletedProcess([], -15)
        ) as run:
            self.assertEqual(verify.execute(checks), 143)
            self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
