"""Synthetic orchestration fixtures; never stand-ins for real model validation."""

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from verify_cpu_benchmark import (
    CPU_DISPATCH,
    CPU_TARGET,
    cpu_target_errors,
    differing_paths,
    main,
    pcse_database_context,
    record_context,
    runtime_context,
    verify,
)


def valid_cpu_context():
    return {
        "system": "Linux",
        "machine": "x86_64",
        "numpy_simd": {
            "X86_V3": True,
            "X86_V4": False,
            "AVX512_ICL": False,
            "AVX512_SPR": False,
        },
        "numpy_baseline": ["X86_V2"],
        "numpy_dispatch": sorted(CPU_DISPATCH),
    }


class BenchmarkArtifacts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.baseline = self.root / "baseline.json"
        self.baseline.write_text('{"synthetic_fixture": [1, 2], "python": "test-A"}\n')
        self.original = self.baseline.read_bytes()
        self.expected = json.loads(self.original)
        self.compute = Mock(return_value=self.expected)

    def run_check(self, output=None, stabilize=lambda value: value):
        with redirect_stdout(io.StringIO()):
            return verify(
                self.baseline,
                output or self.root / "fresh",
                compute=self.compute,
                stabilize=stabilize,
                context=lambda: {"fixture": "explicit synthetic IO test"},
            )

    def test_match_saves_same_execution_and_passes(self):
        self.assertEqual(self.run_check(), 0)
        self.compute.assert_called_once_with()
        self.assertEqual(json.loads((self.root / "fresh/actual.json").read_text()), self.expected)
        comparison = json.loads((self.root / "fresh/comparison.json").read_text())
        self.assertEqual(comparison["outcome"], "PASS")
        self.assertEqual(comparison["differing_paths"], [])
        self.assertEqual(self.baseline.read_bytes(), self.original)

    def test_mismatch_fails_but_saves_exact_actual_before_comparison(self):
        actual = self.expected | {"python": "test-B"}
        self.compute.return_value = actual
        calls = []

        def inspect_saved(value):
            self.assertEqual(json.loads((self.root / "fresh/actual.json").read_text()), actual)
            calls.append(value)
            return value

        self.assertEqual(self.run_check(stabilize=inspect_saved), 1)
        self.compute.assert_called_once_with()
        self.assertEqual(calls, [self.expected, actual])
        comparison = json.loads((self.root / "fresh/comparison.json").read_text())
        self.assertEqual(comparison["outcome"], "FAIL")
        self.assertEqual(comparison["differing_paths"], ["$/python"])
        saved = (self.root / "fresh/actual.json").read_bytes()
        self.assertEqual(comparison["actual_sha256"], hashlib.sha256(saved).hexdigest())
        self.assertEqual(self.baseline.read_bytes(), self.original)

    def test_existing_directory_or_receipt_cannot_be_reused(self):
        self.assertEqual(self.run_check(), 0)
        first = (self.root / "fresh/actual.json").read_bytes()
        self.compute.reset_mock()
        with self.assertRaises(FileExistsError):
            self.run_check()
        self.compute.assert_not_called()
        self.assertEqual((self.root / "fresh/actual.json").read_bytes(), first)

    def test_file_and_canonical_symlink_hardlink_aliases_fail_before_compute(self):
        symlink = self.root / "symlink"
        symlink.symlink_to(self.baseline)
        hardlink = self.root / "hardlink"
        os.link(self.baseline, hardlink)
        existing_dir = self.root / "exists"
        existing_dir.mkdir()
        directory_link = self.root / "directory-link"
        directory_link.symlink_to(existing_dir, target_is_directory=True)
        dangling = self.root / "dangling"
        dangling.symlink_to(self.root / "not-created")
        for path in (
            self.baseline,
            self.root / "exists/../baseline.json",
            symlink,
            hardlink,
            existing_dir,
            directory_link,
            dangling,
        ):
            with self.subTest(path=path.name), self.assertRaises(FileExistsError):
                self.run_check(path)
            self.compute.assert_not_called()
            self.assertEqual(self.baseline.read_bytes(), self.original)
        self.assertFalse((self.root / "not-created").exists())

    def test_missing_or_invalid_baseline_never_computes(self):
        self.baseline.unlink()
        with self.assertRaises(FileNotFoundError):
            self.run_check()
        self.baseline.write_text("not json")
        with self.assertRaises(ValueError):
            self.run_check()
        self.compute.assert_not_called()
        self.assertFalse((self.root / "fresh").exists())

    def test_compute_exception_never_retries_or_claims_pass(self):
        self.compute.side_effect = RuntimeError("synthetic fixture error")
        with self.assertRaises(RuntimeError):
            self.run_check()
        self.compute.assert_called_once_with()
        self.assertTrue((self.root / "fresh/context.json").exists())
        self.assertEqual((self.root / "fresh/comparison.json").read_text(), "")

    def test_runtime_context_is_allowlisted_without_environment_or_host_details(self):
        canary = "PRIVATE_ENVIRONMENT_CANARY"
        synthetic_numpy = SimpleNamespace(
            _core=SimpleNamespace(
                _multiarray_umath=SimpleNamespace(
                    __cpu_features__={
                        "AVX2": True,
                        "SSE2": False,
                        "private-url": canary,
                        "BADTYPE": canary,
                    },
                    __cpu_baseline__=["X86_V2", "private-url", 123],
                    __cpu_dispatch__=["X86_V3", "https://private.invalid", canary * 10],
                )
            )
        )
        with (
            patch.dict(os.environ, {"TEST_PRIVATE_SECRET": canary}),
            patch.dict(sys.modules, {"numpy": synthetic_numpy}),
            patch("verify_cpu_benchmark.importlib.metadata.version", return_value="test-version"),
        ):
            result = runtime_context()
        self.assertEqual(
            set(result),
            {
                "python",
                "implementation",
                "system",
                "machine",
                "libc",
                "distributions",
                "numpy_simd",
                "numpy_baseline",
                "numpy_dispatch",
            },
        )
        self.assertEqual(result["numpy_simd"], {"AVX2": True, "SSE2": False})
        self.assertEqual(result["numpy_baseline"], ["X86_V2"])
        self.assertEqual(result["numpy_dispatch"], ["X86_V3"])
        self.assertNotIn(canary, json.dumps(result))
        self.assertEqual(set(result["distributions"]), {"numpy", "scipy", "aquacrop", "pcse"})

    def test_pcse_context_does_not_import_or_create_when_missing(self):
        with patch.dict(sys.modules, {"pcse": None}), patch("builtins.__import__") as importer:
            self.assertEqual(pcse_database_context(), {"status": "not_loaded"})
        importer.assert_not_called()
        loaded = SimpleNamespace(settings=SimpleNamespace(PCSE_USER_HOME=str(self.root / "absent")))
        with patch.dict(sys.modules, {"pcse": loaded}):
            self.assertEqual(pcse_database_context(), {"status": "missing"})
        self.assertFalse((self.root / "absent").exists())

    def test_loaded_pcse_context_returns_only_hash_size_without_path_or_contents(self):
        home = self.root / "PRIVATE_USERNAME_CANARY"
        home.mkdir()
        database = home / "pcse.db"
        content = b"Explicit synthetic DB bytes: PRIVATE_CONTENT_CANARY"
        database.write_bytes(content)
        loaded = SimpleNamespace(settings=SimpleNamespace(PCSE_USER_HOME=str(home)))
        imports = []
        original_import = __import__

        def observed_import(name, *args, **kwargs):
            imports.append(name)
            return original_import(name, *args, **kwargs)

        with (
            patch.dict(sys.modules, {"pcse": loaded}),
            patch("builtins.__import__", observed_import),
        ):
            result = pcse_database_context()
        self.assertFalse(any(name == "pcse" or name.startswith("pcse.") for name in imports))
        self.assertEqual(
            result,
            {
                "status": "available",
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            },
        )
        self.assertNotIn("PRIVATE_", json.dumps(result))
        self.assertEqual(database.read_bytes(), content)

    def test_pcse_settings_io_failure_and_nonregular_input_are_safe(self):
        loaded = SimpleNamespace(settings=SimpleNamespace(PCSE_USER_HOME=str(self.root)))
        with patch.dict(sys.modules, {"pcse": loaded}):
            with patch("verify_cpu_benchmark.os.open", side_effect=OSError("PRIVATE_ERROR_CANARY")):
                self.assertEqual(pcse_database_context(), {"status": "unavailable"})
            os.mkfifo(self.root / "pcse.db")
            self.assertEqual(pcse_database_context(), {"status": "outside_read_bound"})
        with patch.dict(sys.modules, {"pcse": SimpleNamespace(settings=None)}):
            self.assertEqual(pcse_database_context(), {"status": "settings_unavailable"})

    def test_optional_context_failure_cannot_hide_saved_actual_or_strict_mismatch(self):
        actual = self.expected | {"python": "test-B"}
        self.compute.return_value = actual

        def unavailable():
            self.assertEqual(json.loads((self.root / "fresh/actual.json").read_text()), actual)
            raise OSError("PRIVATE_OPTIONAL_CONTEXT_CANARY")

        with redirect_stdout(io.StringIO()) as output:
            code = verify(
                self.baseline,
                self.root / "fresh",
                compute=self.compute,
                stabilize=lambda value: value,
                context=unavailable,
            )
        self.assertEqual(code, 1)
        self.compute.assert_called_once_with()
        self.assertNotIn("PRIVATE_", output.getvalue())
        metadata = json.loads((self.root / "fresh/context.json").read_text())
        self.assertEqual(metadata["runtime_context_status"], "unavailable")
        self.assertNotIn("PRIVATE_", json.dumps(metadata))
        self.assertEqual(
            json.loads((self.root / "fresh/comparison.json").read_text())["outcome"], "FAIL"
        )

    def test_target_accepts_only_known_effective_platform_and_dispatch(self):
        valid = valid_cpu_context()
        self.assertEqual(cpu_target_errors(valid, CPU_TARGET), [])
        self.assertEqual(cpu_target_errors({}, None), [])
        self.assertTrue(cpu_target_errors(valid, "unknown-target"))
        for name, value in valid["numpy_simd"].items():
            for wrong in (not value, None, int(value), str(value)):
                with self.subTest(feature=name, wrong=wrong):
                    changed = valid | {"numpy_simd": valid["numpy_simd"] | {name: wrong}}
                    self.assertIn("invalid_" + name, cpu_target_errors(changed, CPU_TARGET))
        for key, wrongs in {
            "system": ["Darwin", None],
            "machine": ["aarch64", None],
            "numpy_simd": [None, {}, []],
            "numpy_baseline": [None, [], ["X86_V4"]],
            "numpy_dispatch": [None, [], ["X86_V3"], [None], list(CPU_DISPATCH) + ["NEW_TARGET"]],
        }.items():
            for wrong in wrongs:
                with self.subTest(key=key, wrong=wrong):
                    self.assertTrue(cpu_target_errors(valid | {key: wrong}, CPU_TARGET))
        for key in valid:
            self.assertTrue(
                cpu_target_errors({k: v for k, v in valid.items() if k != key}, CPU_TARGET)
            )

    def test_report_equality_does_not_bypass_wrong_or_missing_cpu_context(self):
        contexts = [valid_cpu_context(), {}, None, {"numpy_simd": {"X86_V3": True}}]
        contexts += [valid_cpu_context() | {"numpy_simd": {"X86_V3": True, "X86_V4": True}}]
        for index, metadata in enumerate(contexts):
            destination = self.root / f"target-{index}"
            self.compute.reset_mock()
            with self.subTest(metadata=metadata), redirect_stdout(io.StringIO()):
                code = verify(
                    self.baseline,
                    destination,
                    compute=self.compute,
                    stabilize=lambda value: value,
                    context=lambda: metadata,
                    cpu_target=CPU_TARGET,
                )
                self.assertEqual(code, 0 if index == 0 else 1)
                self.compute.assert_called_once_with()
                comparison = json.loads((destination / "comparison.json").read_text())
                self.assertEqual(comparison["differing_paths"], [])
                self.assertEqual(bool(comparison["cpu_target_errors"]), index != 0)
                self.assertEqual(
                    json.loads((destination / "actual.json").read_text()), self.expected
                )

    def test_required_context_failure_retains_matching_actual_but_fails(self):
        with redirect_stdout(io.StringIO()):
            code = verify(
                self.baseline,
                self.root / "target-failure",
                compute=self.compute,
                stabilize=lambda value: value,
                context=Mock(side_effect=OSError("PRIVATE_CANARY")),
                cpu_target=CPU_TARGET,
            )
        self.assertEqual(code, 1)
        self.compute.assert_called_once_with()
        comparison = json.loads((self.root / "target-failure/comparison.json").read_text())
        self.assertEqual(comparison["outcome"], "FAIL")
        self.assertTrue(comparison["cpu_target_errors"])
        self.assertNotIn("PRIVATE_CANARY", (self.root / "target-failure/context.json").read_text())

    def test_valid_target_does_not_bypass_strict_numeric_or_identity_difference(self):
        for index, actual in enumerate(
            [
                self.expected | {"synthetic_fixture": [1, 2.0000000000000004]},
                self.expected | {"python": "wrong-interpreter"},
                self.expected | {"input_sha256": "different-input"},
            ]
        ):
            with self.subTest(actual=actual), redirect_stdout(io.StringIO()):
                code = verify(
                    self.baseline,
                    self.root / f"strict-{index}",
                    compute=lambda: actual,
                    stabilize=lambda value: value,
                    context=valid_cpu_context,
                    cpu_target=CPU_TARGET,
                )
                self.assertEqual(code, 1)

    def test_native_context_binds_report_without_model_or_release_verdict(self):
        destination = self.root / "native-context.json"
        with patch("verify_cpu_benchmark.runtime_context", return_value=valid_cpu_context()):
            self.assertEqual(record_context(destination, self.baseline), 0)
        result = json.loads(destination.read_text())
        self.assertEqual(result["actual_sha256"], hashlib.sha256(self.original).hexdigest())
        self.assertEqual(result["purpose"], "native_control_observation_not_release_check")
        self.assertNotIn("outcome", result)
        self.assertEqual(self.baseline.read_bytes(), self.original)
        self.compute.assert_not_called()

    def test_native_context_rejects_existing_and_alias_destinations(self):
        link = self.root / "link"
        link.symlink_to(self.baseline)
        hardlink = self.root / "hardlink"
        os.link(self.baseline, hardlink)
        dangling = self.root / "dangling"
        dangling.symlink_to(self.root / "absent")
        with patch("verify_cpu_benchmark.runtime_context", return_value=valid_cpu_context()):
            for path in [self.baseline, link, hardlink, dangling, self.root / "./baseline.json"]:
                with self.subTest(path=path), self.assertRaises(FileExistsError):
                    record_context(path, self.baseline)
                self.assertEqual(self.baseline.read_bytes(), self.original)
        self.assertFalse((self.root / "absent").exists())

    def test_native_context_failure_never_creates_or_fabricates_metadata(self):
        destination = self.root / "native-context.json"
        with patch("verify_cpu_benchmark.runtime_context", side_effect=OSError("unavailable")):
            with self.assertRaises(OSError):
                record_context(destination, self.baseline)
        self.assertFalse(destination.exists())
        self.assertEqual(self.baseline.read_bytes(), self.original)

    def test_cli_rejects_conflicting_or_incomplete_modes_before_work(self):
        for args in [
            [],
            ["--check", str(self.baseline)],
            ["--record-context", "destination"],
            [
                "--record-context",
                "destination",
                "--actual-report",
                str(self.baseline),
                "--cpu-target",
                CPU_TARGET,
            ],
            [
                "--record-context",
                "destination",
                "--actual-report",
                str(self.baseline),
                "--check",
                str(self.baseline),
            ],
            [
                "--check",
                str(self.baseline),
                "--output-dir",
                "destination",
                "--actual-report",
                str(self.baseline),
            ],
        ]:
            with (
                self.subTest(args=args),
                patch("verify_cpu_benchmark.verify") as checker,
                patch("verify_cpu_benchmark.record_context") as recorder,
                patch("sys.stderr", io.StringIO()),
                self.assertRaises(SystemExit) as error,
            ):
                main(args)
            self.assertEqual(error.exception.code, 2)
            checker.assert_not_called()
            recorder.assert_not_called()

    def test_diagnostic_paths_are_bounded_and_never_include_values(self):
        canary = "TEST_SECRET_MUST_NOT_APPEAR_IN_PATHS"
        result = differing_paths({}, {str(n): canary for n in range(100)})
        self.assertEqual(len(result["differing_paths"]), 50)
        self.assertTrue(result["paths_truncated"])
        self.assertNotIn(canary, json.dumps(result))
        self.assertEqual(
            differing_paths({"a/b": [1]}, {"a/b": [2]}),
            {
                "differing_paths": ["$/a~1b/0"],
                "paths_truncated": False,
            },
        )
        self.assertEqual(len(differing_paths({}, {"x" * 400: 1})["differing_paths"][0]), 256)


if __name__ == "__main__":
    unittest.main()
