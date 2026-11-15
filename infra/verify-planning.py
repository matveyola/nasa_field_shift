#!/usr/bin/env python3
"""Disposable, authenticated A→B→C→D acceptance. Never uses the user's stack/data.

Builds unique images, runs the real private CPU model on explicitly synthetic inputs,
checks apply/replan/restart and restores an actual archive into another database.
No external data or paid inference calls. Failure preserves a red report and logs.
"""

import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import secrets
import shlex
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-build", action="store_true", help="Use explicitly supplied image tags"
    )
    args = parser.parse_args()
    project = os.getenv("PLANNING_VERIFY_PROJECT", "planning-verify-" + secrets.token_hex(4))
    if not re.fullmatch(r"planning-(verify|ci)(-[a-z0-9]+)?", project):
        raise ValueError("Use an isolated planning-verify-* or planning-ci project")
    report_dir = Path(os.getenv("PLANNING_VERIFY_OUTPUT", "/tmp/field-shift-planning-verification"))
    report_dir.mkdir(parents=True, exist_ok=True)
    report_dir.chmod(0o700)
    report = {
        "project": project,
        "technical": "failed",
        "empirical": "not_validated",
        "checks": [],
        "image_mode": "explicit_prebuilt_tags" if args.no_build else "built_in_this_run",
    }
    docker = ["docker"]
    if subprocess.run(
        docker + ["info"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode:
        docker = ["sg", "docker", "-c"]

    def command(parts, *, input=None, env=None, capture=False):
        if parts[0] == "docker" and len(docker) > 1:
            parts = docker + [shlex.join(parts)]
        with (report_dir / "commands.log").open("ab") as log:
            result = subprocess.run(
                parts,
                check=False,
                cwd=ROOT,
                input=input,
                env=env,
                stdout=subprocess.PIPE if capture else log,
                stderr=log,
            )
        if result.returncode:
            raise RuntimeError(f"Verification command failed ({parts[0]}); inspect commands.log")
        return result.stdout.decode() if capture else None

    # Require a fresh project; even a previous failed verifier is not silently overwritten.
    present = command(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=com.docker.compose.project=" + project,
        ],
        capture=True,
    )
    if present.strip():
        raise RuntimeError("Project already exists; select another unique PLANNING_VERIFY_PROJECT")
    web_port, pg_port, redis_port = free_port(), free_port(), free_port()
    password, token, bootstrap, login_password = [secrets.token_hex(24) for _ in range(4)]
    base = f"http://127.0.0.1:{web_port}"
    with tempfile.TemporaryDirectory(prefix=project + "-") as private:
        private = Path(private)
        envfile = private / "compose.env"
        settings = {
            "POSTGRES_USER": "field_shift",
            "POSTGRES_DB": "planning_acceptance",
            "POSTGRES_PASSWORD": password,
            "FIELD_SHIFT_CROP_MODEL_TOKEN": token,
            "FIELD_SHIFT_CROP_MODEL_URL": "http://crop-model:8001",
            "FIELD_SHIFT_ACCESS_MODE": "team",
            "FIELD_SHIFT_PUBLIC_ORIGIN": base,
            "FIELD_SHIFT_BOOTSTRAP_TOKEN": bootstrap,
            "WEB_PORT": str(web_port),
            "FIELD_SHIFT_BACKUP_DIR": str(private / "backups"),
            "API_IMAGE": os.getenv("PLANNING_VERIFY_API_IMAGE", f"field-shift-api:{project}"),
            "WEB_IMAGE": os.getenv("PLANNING_VERIFY_WEB_IMAGE", f"field-shift-web:{project}"),
            "CROP_MODEL_IMAGE": os.getenv(
                "PLANNING_VERIFY_MODEL_IMAGE", f"field-shift-crop-model:{project}"
            ),
            "AGENT_IMAGE": f"field-shift-agent:{project}",
        }
        if args.no_build and any(
            not os.getenv(k)
            for k in [
                "PLANNING_VERIFY_API_IMAGE",
                "PLANNING_VERIFY_WEB_IMAGE",
                "PLANNING_VERIFY_MODEL_IMAGE",
            ]
        ):
            raise ValueError("--no-build requires all three explicit verification image tags")
        envfile.write_text("".join(f"{k}={v}\n" for k, v in settings.items()))
        envfile.chmod(0o600)
        override = private / "ports.json"
        override.write_text(
            json.dumps(
                {
                    "services": {
                        "postgres": {"ports": [f"127.0.0.1:{pg_port}:5432"]},
                        "redis": {"ports": [f"127.0.0.1:{redis_port}:6379"]},
                        # The planning acceptance uses supplied field geometry.
                        # Boundary inference has its own real-image smoke test;
                        # do not download weights in this deterministic gate.
                        "boundary-model": {"profiles": ["verify-boundary-inference"]},
                    }
                }
            )
        )
        prefix = [
            "docker",
            "compose",
            "--env-file",
            str(envfile),
            "-f",
            "compose.yaml",
            "-f",
            str(override),
            "-p",
            project,
        ]
        # Shell variables must not override this isolated env file.
        original = {k: os.environ.pop(k) for k in settings if k in os.environ}

        def compose(*parts, **kwargs):
            return command(prefix + ["--profile", "crop-model", *parts], **kwargs)

        def sql(statement, database=None):
            db = database or settings["POSTGRES_DB"]
            return compose(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "field_shift",
                "-d",
                db,
                "-Atc",
                statement,
                capture=True,
            ).strip()

        def check(name):
            report["checks"].append(name)
            print("PASS", name, flush=True)

        cookies = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
        csrf = ""

        def api(path, body=None, expected=200):
            req = urllib.request.Request(
                base + "/api" + path,
                data=None if body is None else json.dumps(body).encode(),
                headers={
                    "Content-Type": "application/json",
                    "X-Field-Shift-Request": "1",
                    "X-CSRF-Token": csrf,
                    "Origin": base,
                },
            )
            try:
                response = opener.open(req, timeout=25)
            except urllib.error.HTTPError as exc:
                response = exc
            data = json.load(response)
            if response.code != expected:
                raise AssertionError(f"{path}: expected {expected}, received {response.code}")
            return data

        def wait_run(run):
            deadline = time.monotonic() + 180
            while run["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(1)
                run = api("/planning-v1/farms/1/runs/" + run["run_id"])
            assert run["status"] == "succeeded", (run["status"], run.get("error"))
            assert run["comparison"]["alternatives"]
            return run

        started = False
        try:
            command(prefix + ["config", "--quiet"])
            command(prefix + ["--profile", "agent", "--profile", "crop-model", "config", "--quiet"])
            if not args.no_build:
                compose("build", "api", "web", "crop-model")
            check("default and optional profiles; isolated image configuration")
            started = True
            compose(
                "up",
                "-d",
                "postgres",
                "redis",
                "crop-model",
                "api",
                "dispatcher",
                "worker-planning",
                "web",
            )
            deadline = time.monotonic() + 100
            while True:
                try:
                    api("/health")
                    break
                except (OSError, ValueError):
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(1)
            check("cold start and health")
            api("/planning-workspace", expected=401)
            api(
                "/auth/setup",
                {
                    "name": "Synthetic acceptance",
                    "email": "acceptance@example.test",
                    "password": login_password,
                    "token": bootstrap,
                },
            )
            csrf = api("/auth/session")["csrf_token"]
            assert csrf
            assert api("/planning-workspace")["request"] is None
            check("authenticated access and explicit missing-input state")
            compose(
                "run",
                "--rm",
                "--no-deps",
                "-v",
                str(ROOT) + ":/verification:ro",
                "api",
                "python",
                "/verification/apps/api/tests/planning_runtime/live_fixture.py",
                "--confirm-synthetic-test-database",
            )
            boot = api("/planning-workspace")
            assert boot["status"] == "synthetic"
            run = wait_run(api("/planning-v1/farms/1/runs", boot["request"], 202))
            report["initial_seconds"] = run["comparison"]["elapsed_seconds"]
            report["run_id"] = run["run_id"]
            report["input_sha256"] = run["input_sha256"]
            evidence = api("/planning-workspace/farms/1/runs/" + run["run_id"] + "/evidence")
            assert evidence["model_card"]["promotion"] == "baseline_only"
            assert any(e["applicability"] == "baseline" for e in evidence["evaluations"])
            assert any(e["applicability"] == "unsupported" for e in evidence["evaluations"])
            assert all(e["applicability"] != "validated" for e in evidence["evaluations"])
            report["model"] = evidence["model_card"]["model"]
            check("two fields, three seasons, real CPU evaluations, alternatives and abstentions")
            apply = {
                "schema_version": "1.0.0",
                "request_id": str(uuid4()),
                "farm_id": "1",
                "run_id": run["run_id"],
                "alternative_id": run["comparison"]["alternatives"][0]["alternative_id"],
                "expected_input_sha256": run["input_sha256"],
                "expected_snapshots": run["comparison"]["snapshots"],
                "confirmed_by_human": True,
            }
            path = "/planning-v1/farms/1/runs/" + run["run_id"]
            assert api(path + "/apply", apply)["status"] == "applied"
            assert api(path + "/apply", apply)["status"] == "already_applied"
            fresh = api("/planning-workspace")["request"]
            assert len(fresh["locks"]) == 6
            replan = wait_run(
                api(
                    path + "/replan",
                    {
                        "schema_version": "1.0.0",
                        "previous_run_id": run["run_id"],
                        "request": fresh,
                        "preserve_commitment_ids": [a["commitment_id"] for a in fresh["locks"]],
                    },
                    202,
                )
            )
            assert replan["comparison"]["alternatives"][0]["assignments"] == fresh["locks"]
            check("human-confirmed apply, idempotency and commitment-preserving replan")
            # Exercise the actual frontend against the authenticated stack, without mocked HTTP.
            browser_env = dict(
                os.environ,
                PLANNING_LIVE_URL=base,
                PLANNING_LIVE_PASSWORD=login_password,
                PLANNING_LIVE_OUTPUT=str(report_dir),
            )
            command(["node", "infra/verify-planning-browser.mjs"], env=browser_env)
            check("live desktop/mobile UI, evidence, confirmation and replan diff")
            compose("restart", "api", "dispatcher", "worker-planning")
            for attempt in range(60):
                try:
                    restored = api(path)
                    break
                except (OSError, ValueError):
                    if attempt == 59:
                        raise
                    time.sleep(1)
            assert (
                restored["input_sha256"] == run["input_sha256"]
                and restored["status"] == "succeeded"
            )
            check("saved plans survive API/worker/dispatcher restart")
            counts_query = (
                "select (select count(*) from fields), (select count(*) from planning_v1_runs), "
                "(select count(*) from planning_v1_commitments), "
                "(select count(*) from planning_field_profiles)"
            )
            original_counts = sql(counts_query)
            compose(
                "exec",
                "-T",
                "postgres",
                "pg_dump",
                "-U",
                "field_shift",
                "-d",
                settings["POSTGRES_DB"],
                "-Fc",
                "-f",
                "/tmp/planning-acceptance.dump",
            )
            compose(
                "exec",
                "-T",
                "postgres",
                "createdb",
                "-U",
                "field_shift",
                "planning_restore",
            )
            compose(
                "exec",
                "-T",
                "postgres",
                "pg_restore",
                "--exit-on-error",
                "-U",
                "field_shift",
                "-d",
                "planning_restore",
                "/tmp/planning-acceptance.dump",
            )
            assert sql(counts_query, "planning_restore") == original_counts
            heads = compose("exec", "-T", "api", "alembic", "heads", capture=True)
            assert heads.count("(head)") == 1 and "0016_merge_agent_planning" in heads
            script = """import os, subprocess
from field_shift.config import get_settings
from sqlalchemy.engine import make_url
url = make_url(get_settings().database_url).set(database="planning_restore")
os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
subprocess.run(["alembic", "downgrade", "0010_precision_plans"], check=True)
"""
            compose("exec", "-T", "api", "python", "-", input=script.encode())
            assert (
                sql("select count(*) from fields", "planning_restore")
                == original_counts.split("|")[0]
            )
            compose(
                "exec",
                "-T",
                "api",
                "python",
                "-",
                input=script.replace(
                    '"downgrade", "0010_precision_plans"', '"upgrade", "head"'
                ).encode(),
            )
            assert (
                sql("select version_num from alembic_version", "planning_restore")
                == "0016_merge_agent_planning"
            )
            check(
                "single migration head; archive restored to fresh DB; "
                "downgrade/upgrade preserves baseline"
            )
            # Separate disposable databases for existing PostgreSQL/Redis concurrency suites.
            for name in ["planning_test", "planning_profiles"]:
                compose("exec", "-T", "postgres", "createdb", "-U", "field_shift", name)
            test_env = dict(
                os.environ,
                FIELD_SHIFT_TEST_DATABASE_URL=f"postgresql+psycopg://field_shift:{password}@127.0.0.1:{pg_port}/planning_test",
                ISSUE3_DATABASE_URL=f"postgresql+psycopg://field_shift:{password}@127.0.0.1:{pg_port}/planning_profiles",
                ISSUE5_POSTGRES_ADMIN_URL=f"postgresql://field_shift:{password}@127.0.0.1:{pg_port}/planning_test",
                FIELD_SHIFT_TEST_REDIS_URL=f"redis://127.0.0.1:{redis_port}/15",
                FIELD_SHIFT_HANDOFF_TEST_DATABASE_URL=f"postgresql+psycopg://field_shift:{password}@127.0.0.1:{pg_port}/planning_test",
            )
            command(
                [
                    str(ROOT / "apps/api/.venv/bin/python"),
                    "-m",
                    "pytest",
                    "-q",
                    "-m",
                    "integration",
                    "apps/api/tests",
                ],
                env=test_env,
            )
            check("all impacted PostgreSQL/Redis integration and concurrency tests")
            command(
                [
                    str(ROOT / "apps/api/.venv/bin/python"),
                    "-m",
                    "pytest",
                    "-q",
                    "apps/api/tests/test_rotation_handoff.py",
                ],
                env=test_env,
            )
            check("handoff owner, snapshot and final-attempt fences on PostgreSQL")
            compose("stop", "crop-model")
            unavailable = api("/planning-workspace")
            assert unavailable["status"] == "unavailable" and unavailable["request"] is None
            check("model outage returns unavailable without replacement data")
            report["technical"] = "passed"
        finally:
            report["contracts_sha256"] = (
                hashlib.sha256(
                    (ROOT / "contracts/planning-v1/planning-v1.schema.json").read_bytes()
                ).hexdigest()
                if (ROOT / "contracts/planning-v1/planning-v1.schema.json").exists()
                else None
            )
            (report_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            if started:
                compose(
                    "logs",
                    "--no-color",
                    "--tail",
                    "100",
                    "worker-planning",
                    "crop-model",
                    "api",
                )
                compose("down", "--volumes", "--remove-orphans")
            os.environ.update(original)
    print("Report:", report_dir / "report.json", flush=True)


if __name__ == "__main__":
    main()
