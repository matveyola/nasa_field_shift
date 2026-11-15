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

from verify_cpu_benchmark import differing_paths, pcse_database_context, runtime_context, verify


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
                    }
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
            },
        )
        self.assertEqual(result["numpy_simd"], {"AVX2": True, "SSE2": False})
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
