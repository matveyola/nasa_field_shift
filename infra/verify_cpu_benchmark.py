"""Retain one actual installed benchmark before its authoritative strict comparison."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import stat
import sys
from contextlib import ExitStack
from pathlib import Path


def differing_paths(expected, actual, *, limit=50):
    """Bounded diagnostic paths only; this never decides benchmark equality."""
    paths = []
    truncated = False

    def add(path):
        nonlocal truncated
        if len(paths) == limit:
            truncated = True
        else:
            paths.append(path[:256])

    def visit(left, right, path, depth=0):
        if truncated or left == right:
            return
        if depth >= 64:
            add(path)
        elif isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(left.keys() | right.keys()):
                child = path + "/" + key.replace("~", "~0").replace("/", "~1")
                if key not in left or key not in right:
                    add(child)
                else:
                    visit(left[key], right[key], child, depth + 1)
                if truncated:
                    break
        elif isinstance(left, list) and isinstance(right, list):
            if len(left) != len(right):
                add(path + "/length")
            for index, (a, b) in enumerate(zip(left, right)):
                visit(a, b, path + "/" + str(index), depth + 1)
                if truncated:
                    break
        else:
            add(path)

    visit(expected, actual, "$")
    return {"differing_paths": paths, "paths_truncated": truncated}


CPU_TARGET = "x86-64-v3"
CPU_DISPATCH = {"X86_V3", "X86_V4", "AVX512_ICL", "AVX512_SPR"}


def feature_name(value):
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 32
        and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in value)
    )


def cpu_target_errors(metadata, target):
    """Fail closed for the declared pinned x86 wheel's effective dispatch."""
    if target is None:
        return []
    if target != CPU_TARGET:
        return ["unknown_cpu_target"]
    errors = []
    if metadata.get("system") != "Linux" or metadata.get("machine") != "x86_64":
        errors.append("unsupported_platform")
    features = metadata.get("numpy_simd")
    if not isinstance(features, dict):
        features = {}
    for name, required in {
        "X86_V3": True,
        "X86_V4": False,
        "AVX512_ICL": False,
        "AVX512_SPR": False,
    }.items():
        if features.get(name) is not required:
            errors.append("invalid_" + name)
    if metadata.get("numpy_baseline") != ["X86_V2"]:
        errors.append("unexpected_numpy_baseline")
    dispatch = metadata.get("numpy_dispatch")
    if (
        not isinstance(dispatch, list)
        or any(not isinstance(name, str) for name in dispatch)
        or len(dispatch) != len(CPU_DISPATCH)
        or set(dispatch) != CPU_DISPATCH
    ):
        errors.append("unexpected_numpy_dispatch")
    return errors


def runtime_context():
    """Fixed safe metadata, excluding environment, hostname, paths and build settings."""
    import numpy

    libc, version = platform.libc_ver()
    runtime = numpy._core._multiarray_umath
    features = getattr(runtime, "__cpu_features__", {})
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "system": platform.system(),
        "machine": platform.machine(),
        "libc": {"name": libc, "version": version},
        "distributions": {
            name: importlib.metadata.version(name)
            for name in ("numpy", "scipy", "aquacrop", "pcse")
        },
        "numpy_simd": {
            key: value
            for key, value in sorted(features.items())
            if feature_name(key) and isinstance(value, bool)
        },
        "numpy_baseline": [
            name for name in getattr(runtime, "__cpu_baseline__", []) if feature_name(name)
        ],
        "numpy_dispatch": [
            name for name in getattr(runtime, "__cpu_dispatch__", []) if feature_name(name)
        ],
    }


def pcse_database_context():
    """Fingerprint only an already configured, regular DB; never import PCSE."""
    loaded = sys.modules.get("pcse")
    if loaded is None:
        return {"status": "not_loaded"}
    try:
        settings = vars(loaded).get("settings")
        home = vars(settings).get("PCSE_USER_HOME") if settings is not None else None
        if not isinstance(home, str) or not Path(home).is_absolute():
            return {"status": "settings_unavailable"}
        path = Path(home) / "pcse.db"
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as source:
            before = os.fstat(source.fileno())
            maximum = 64 * 1024 * 1024
            if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
                return {"status": "outside_read_bound"}
            digest, size = hashlib.sha256(), 0
            while chunk := source.read(min(1024 * 1024, maximum - size + 1)):
                size += len(chunk)
                if size > maximum:
                    return {"status": "outside_read_bound"}
                digest.update(chunk)
            after = os.fstat(source.fileno())
            if size != before.st_size or (before.st_size, before.st_mtime_ns) != (
                after.st_size,
                after.st_mtime_ns,
            ):
                return {"status": "changed_during_read"}
        return {"status": "available", "sha256": digest.hexdigest(), "size_bytes": size}
    except FileNotFoundError:
        return {"status": "missing"}
    except Exception:
        # Optional diagnostics must not replace the strict result. No exception
        # text/traceback can leak a configured path, username or file contents.
        return {"status": "unavailable"}


def verify(baseline, output_dir, *, compute, stabilize, context, cpu_target=None):
    """Reserve a fresh artifact destination before computing exactly once.

    Callables keep tooling tests independent of model dependencies; main always
    supplies the installed authoritative benchmark and unchanged stable_report.
    """
    baseline = Path(baseline).resolve(strict=True)
    baseline_bytes = baseline.read_bytes()
    expected = json.loads(baseline_bytes)
    output_dir = Path(output_dir)
    # Existing files/directories, symlinks (even dangling), and hard-link aliases
    # cannot pass exclusive mkdir. Never clean up or overwrite a previous receipt.
    output_dir.mkdir(mode=0o700)
    with ExitStack() as stack:
        artifacts = {
            name: stack.enter_context((output_dir / name).open("x", encoding="utf-8"))
            for name in ("actual.json", "context.json", "comparison.json")
        }
        actual = compute()
        # Persist and flush the same computed value BEFORE comparing. A mismatch
        # must not discard the only actual execution or cause a second execution.
        encoded = json.dumps(actual, indent=2, allow_nan=False) + "\n"
        artifacts["actual.json"].write(encoded)
        artifacts["actual.json"].flush()
        try:
            metadata = context()
            if not isinstance(metadata, dict):
                raise TypeError("Runtime context must be an object")
        except Exception:
            metadata = {"runtime_context_status": "unavailable"}
        metadata = metadata | {
            "baseline_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
            "pcse_database_after_benchmark": pcse_database_context(),
        }
        artifacts["context.json"].write(json.dumps(metadata, indent=2, allow_nan=False) + "\n")
        artifacts["context.json"].flush()
        old, new = stabilize(expected), stabilize(actual)
        equal = old == new
        target_errors = cpu_target_errors(metadata, cpu_target)
        accepted = equal and not target_errors
        comparison = {
            "outcome": "PASS" if accepted else "FAIL",
            "actual_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
            **differing_paths(old, new),
        }
        if cpu_target is not None:
            comparison["cpu_target"] = cpu_target
            comparison["cpu_target_errors"] = target_errors
        artifacts["comparison.json"].write(json.dumps(comparison, indent=2) + "\n")
    print(json.dumps(comparison))
    return 0 if accepted else 1


def record_context(destination, actual_report):
    """Bind safe native-control metadata to an existing actual report, without computing."""
    actual_bytes = Path(actual_report).read_bytes()
    json.loads(actual_bytes)  # Refuse to bind a missing or malformed report.
    metadata = runtime_context() | {
        "actual_sha256": hashlib.sha256(actual_bytes).hexdigest(),
        "purpose": "native_control_observation_not_release_check",
    }
    with Path(destination).open("x", encoding="utf-8") as output:
        output.write(json.dumps(metadata, indent=2, allow_nan=False) + "\n")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--cpu-target", choices=[CPU_TARGET])
    parser.add_argument("--record-context", type=Path)
    parser.add_argument("--actual-report", type=Path)
    args = parser.parse_args(argv)
    if args.record_context is not None:
        if args.actual_report is None or any((args.check, args.output_dir, args.cpu_target)):
            parser.error("--record-context requires only --actual-report")
        return record_context(args.record_context, args.actual_report)
    if args.check is None or args.output_dir is None or args.actual_report is not None:
        parser.error("strict verification requires --check and --output-dir")
    # Ordinary installed imports; no checkout path injection or package edits.
    from field_shift_models.cli import benchmark, stable_report

    return verify(
        args.check,
        args.output_dir,
        compute=benchmark,
        stabilize=stable_report,
        context=runtime_context,
        cpu_target=args.cpu_target,
    )


if __name__ == "__main__":
    raise SystemExit(main())
