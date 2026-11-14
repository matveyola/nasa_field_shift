"""Verify the agent subsystem before a teammate integrates it. One command, no paid API calls.

    uv run python scripts/verify.py            # everything, incl. the browser tests (needs: npm --prefix frontend install)
    uv run python scripts/verify.py --quick    # skip the frontend build and browser tests

Runs, in order: repository hygiene (LF endings, no secrets, no large or ignored files tracked), ruff, pytest,
the scripted eval suite, contract schema freshness (M0 + integration) and generated TypeScript, TypeScript
type checks, generated validators, frontend unit tests, build, and Playwright with replayed responses.
It never contacts Anthropic or a teammate service. Exit code 1 if any step fails.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
MAX_TRACKED_BYTES = 2_000_000
SECRET = re.compile(r"sk-ant-(?!test)[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----")
FORBIDDEN = re.compile(r"(^|/)(\.env$|data/|node_modules/|\.venv/|dist/|dist-lib/|test-results/)|\.(sqlite|bin|safetensors|bundle)$")


def tool(name: str) -> str:
    # On Windows prefer npm.cmd / npx.cmd: a bare "npm" on PATH can be a POSIX shim (Git Bash) Windows cannot run.
    candidates = (name + ".cmd", name + ".exe", name) if os.name == "nt" else (name,)
    found = next((p for p in map(shutil.which, candidates) if p), None)
    if not found:
        raise SystemExit(f"{name} not found on PATH")
    return found


def hygiene() -> str | None:
    files = subprocess.run(["git", "ls-files", "--eol"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    problems = []
    for line in files.splitlines():
        info, _, path = line.partition("\t")
        if "i/crlf" in info:
            problems.append(f"CRLF in the index: {path}")
        if "w/crlf" in info:  # git would normalise it on commit, but byte-exact checks read the working copy
            problems.append(f"CRLF in the working copy: {path}")
        if FORBIDDEN.search(path):
            problems.append(f"must not be tracked: {path}")
        full = ROOT / path
        if full.is_file():
            if full.stat().st_size > MAX_TRACKED_BYTES:
                problems.append(f"large tracked file ({full.stat().st_size // 1024} KB): {path}")
            elif "-text" not in info and SECRET.search(full.read_text(encoding="utf-8", errors="ignore")):
                problems.append(f"looks like a secret: {path}")
    return "; ".join(problems[:10]) or None


def generated_ts_fresh() -> str | None:
    """Regenerate both TypeScript contract files and report if that changed them (i.e. they were stale)."""
    targets = [ROOT / "contracts/ts/index.d.ts", ROOT / "contracts/ts/integration.d.ts"]
    before = [p.read_bytes() for p in targets]
    for script in ("gen:ts", "gen:ts:integration"):
        subprocess.run([tool("npm"), "run", "--silent", script], cwd=ROOT, check=True, capture_output=True)
    stale = [p.name for p, b in zip(targets, before) if p.read_bytes() != b]
    return f"regenerated and changed: {stale} (commit the regenerated files)" if stale else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quick", action="store_true", help="skip frontend build and browser tests")
    args = parser.parse_args()
    uv, npm, npx = tool("uv"), tool("npm"), tool("npx")
    steps: list[tuple[str, object]] = [
        ("repository hygiene (LF, secrets, size, ignored files)", hygiene),
        ("ruff", [uv, "run", "ruff", "check", "."]),
        ("pytest", [uv, "run", "pytest", "-q"]),
        ("eval suite (scripted, no API calls)", [uv, "run", "python", "-m", "evals.run"]),
        ("contract schemas up to date", [uv, "run", "python", "-m", "contracts.export", "--check"]),
        ("generated TypeScript contracts up to date", generated_ts_fresh),
        ("TypeScript: examples/client.ts", [npm, "run", "--silent", "check:ts"]),
        ("TypeScript: widget + demo", [npm, "--prefix", "frontend", "run", "--silent", "typecheck"]),
        ("generated validators up to date", [npm, "--prefix", "frontend", "run", "--silent", "check:validators"]),
        ("frontend unit and integration tests", [npm, "--prefix", "frontend", "test", "--silent"]),
    ]
    if not args.quick:
        steps += [("frontend build", [npm, "--prefix", "frontend", "run", "--silent", "build"]),
                  ("browser tests (replayed responses)", [npx, "--prefix", "frontend", "playwright", "test",
                                                          "--config", str(FRONTEND / "playwright.config.ts")])]
    failed = []
    for name, action in steps:
        started = time.monotonic()
        if callable(action):
            problem = action()
            ok, detail = problem is None, problem or ""
        else:
            proc = subprocess.run(action, cwd=FRONTEND if "playwright" in action else ROOT, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
            ok = proc.returncode == 0
            tail = [line for line in (proc.stdout + proc.stderr).splitlines() if line.strip()][-3:]
            detail = " | ".join(tail)[-300:]
        print(f"{'PASS' if ok else 'FAIL'}  {name}  ({time.monotonic() - started:.0f} s)" + ("" if ok else f"\n      {detail}"))
        if not ok:
            failed.append(name)
    print("\nOK: all checks passed" if not failed else f"\nFAILED: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
