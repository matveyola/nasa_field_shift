"""The CI change filter (.github/workflows/ci.yml, job `changes`) runs every job a change can break.

A host-side change to how the API builds and signs the agent's context must run the agent's tests,
and a change to the agent's contract must run the API's tests (the API checks its context against
that contract). A migration must run the API, PostgreSQL integration and planning jobs. These tests
read the real awk program and job graph from ci.yml, so an edit that drops a rule, an output or a
Required dependency fails here. The awk program is evaluated in Python; where `awk` is installed
(the CI runner), the real awk must give the same answer.

The team workflow differs by branch (planning, boundary model, package and tooling jobs), so the
jobs are read from the file, and a job is required whenever the repository contains what it tests
(for example `contracts/planning-v1/` requires the `planning` job).
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CI = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")


def _program() -> str:
    match = re.search(r"awk '\n(.*?)\n(\s*)}' \"\$RUNNER_TEMP/changes\"", CI, re.S)
    assert match, "the change filter is no longer an awk program over $RUNNER_TEMP/changes"
    return f"{match.group(1)}\n{match.group(2)}}}\n"


PROGRAM = _program()
# One rule per line: /positive/ [&& !/negative/] { job = 1[; job = 1 ...] }
_RULE = re.compile(
    r"^\s*/(?P<pos>(?:\\/|[^/])+)/(?:\s*&&\s*!/(?P<neg>(?:\\/|[^/])+)/)?\s*"
    r"\{\s*(?P<jobs>\w+\s*=\s*1(?:\s*;\s*\w+\s*=\s*1)*)\s*;?\s*\}\s*$"
)


def _rules(program: str):
    rules = []
    for line in program.splitlines():
        if match := _RULE.match(line):
            jobs = tuple(re.findall(r"(\w+)\s*=\s*1", match["jobs"]))
            neg = re.compile(match["neg"].replace("\\/", "/")) if match["neg"] else None
            rules.append((re.compile(match["pos"].replace("\\/", "/")), neg, jobs, line.strip()))
    return rules


RULES = _rules(PROGRAM)
OUTPUTS = re.findall(r"^\s{6}(\w+): \$\{\{ steps\.detect\.outputs\.(\w+) \}\}", CI, re.M)
GATED = dict(
    re.findall(
        r"^  (\w+):\n(?:    .*\n)*?    if: needs\.changes\.outputs\.(\w+) == 'true'", CI, re.M
    )
)
TOP_JOBS = re.findall(r"^  (\w+):\n", CI.split("\njobs:\n", 1)[1], re.M)
REQUIRED_NEEDS = re.search(r"^  required:\n(?:    .*\n)*?    needs: \[([^\]]*)\]", CI, re.M)
JOBS = tuple(output for output, _ in OUTPUTS)


def _evaluate(rules, paths) -> set[str]:
    return {
        job
        for path in paths
        for pos, neg, jobs, _ in rules
        if pos.search(path) and not (neg and neg.search(path))
        for job in jobs
    }


def jobs_for(*paths: str) -> set[str]:
    """The jobs the CI filter turns on for these changed paths (awk program semantics)."""
    on = _evaluate(RULES, paths)
    if awk := shutil.which("awk"):
        out = subprocess.run(
            [awk, PROGRAM],
            input="\n".join(paths) + "\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout
        assert {k for k, v in (line.split("=") for line in out.split()) if v == "true"} == on, (
            "awk disagrees"
        )
    return on


def has(job: str) -> set[str]:
    return {job} if job in JOBS else set()


# ---------------------------------------------------------------- the filter's shape


def test_every_rule_line_is_understood():
    rule_lines = [line for line in PROGRAM.splitlines() if re.search(r"\w+\s*=\s*1", line)]
    assert len(RULES) == len(rule_lines) >= 10  # no rule silently skipped by this parser


def test_every_output_is_printed_routed_and_gates_exactly_one_job():
    assert [name for name, _ in OUTPUTS] == [source for _, source in OUTPUTS]
    for job in JOBS:
        assert f'print "{job}=" ({job} ? "true" : "false")' in PROGRAM
        assert any(job in jobs for *_, jobs, _ in RULES), f"{job} is never turned on"
    assert sorted(GATED) == sorted(GATED.values()) == sorted(JOBS)


def test_the_jobs_this_repository_needs_exist():
    core = {"api", "agent", "agent_reference", "web", "integration", "ml"}
    assert core <= set(JOBS)
    if (ROOT / "contracts/planning-v1").is_dir() or (ROOT / "packages/field-shift-models").is_dir():
        assert "planning" in JOBS
    if (ROOT / "services/boundary-model").is_dir():
        assert "boundary_model" in JOBS
    if (ROOT / "infra/verify_python_wheels.py").exists():
        assert "python_packages" in TOP_JOBS
    if (ROOT / "infra/verify.py").exists():
        assert "agent_tooling" in TOP_JOBS


def test_the_required_check_waits_for_every_job_and_permissions_stay_read_only():
    assert re.search(r"^permissions:\n  contents: read\n", CI, re.M)
    assert REQUIRED_NEEDS, "the Required checks job is gone"
    needs = {name.strip() for name in REQUIRED_NEEDS.group(1).split(",")}
    assert needs == set(TOP_JOBS) - {"required"}
    assert re.search(r"^  required:\n    name: Required checks\n    if: always\(\)", CI, re.M)
    assert re.search(r"case \"\$result\" in success\|skipped\) ;; \*\) exit 1 ;; esac", CI)


def test_ungated_jobs_always_run():
    for job in set(TOP_JOBS) - set(GATED) - {"changes", "required"}:
        block = re.search(rf"^  {job}:\n((?:    .*\n|\n)*?)(?=^  \w|\Z)", CI, re.M).group(1)
        assert "if:" not in block.split("steps:", 1)[0], f"{job} became conditional"


# ---------------------------------------------------------------- routing

CASES = {
    # The host side of the agent's signed context, and the agent's contract.
    "apps/api/src/field_shift/modules/agent/constraints.py": {"agent", "api", "integration"}
    | has("planning"),
    "apps/api/src/field_shift/modules/agent/field_context.py": {"agent", "api", "integration"}
    | has("planning"),
    "apps/api/src/field_shift/modules/agent/service.py": {"agent", "api", "integration"}
    | has("planning"),
    "apps/api/src/field_shift/modules/agent/signing.py": {"agent", "api", "integration"}
    | has("planning"),
    "services/agent/contracts/models.py": {"agent", "api", "agent_reference"},
    "services/agent/contracts/schema/fieldshift-contracts.schema.json": {
        "agent",
        "api",
        "agent_reference",
    },
    "services/agent/tests/fixtures/host_constraints.json": {"agent", "api"},
    # Migrations run the API, PostgreSQL integration and planning jobs.
    "apps/api/migrations/versions/0015_merge_agent_planning.py": {"api", "integration"}
    | has("planning"),
    "apps/api/alembic.ini": {"api", "integration"} | has("planning"),
    # Existing routing.
    "apps/api/src/field_shift/modules/agent/routes.py": {"api", "integration"} | has("planning"),
    "apps/api/tests/test_domain_api.py": {"api"} | has("planning"),
    "apps/api/tests/integration/test_durable_jobs.py": {"api", "integration"} | has("planning"),
    "apps/api/src/field_shift/modules/rotations/planning_catalog.py": {"api", "integration", "ml"}
    | has("planning"),
    "services/agent/agent_service/tools.py": {"agent"},
    "services/agent/integration_stub/app.py": {"agent"},
    "services/agent/evals/constraint_cases.py": {"agent"},
    "services/agent/evals/reports/scripted-latest.md": set(),  # reports never run the agent
    "services/agent/Dockerfile": {"agent"},
    "services/agent/uv.lock": {"agent"},
    "services/agent/package.json": {"agent_reference"},
    "services/agent/tsconfig.json": {"agent_reference"},
    "services/agent/examples/client.ts": {"agent_reference"},
    "services/agent/frontend/src/widget/ui/Panel.tsx": {"agent_reference", "web"},
    "services/agent/frontend/docs/notes.md": set(),
    "apps/web/src/app/App.tsx": {"web"} | has("planning"),
    "packages/field-shift-ml/src/x.py": {"ml"},
    "infra/configure-agent.py": {"agent", "integration"} | has("planning"),
    "infra/backup.sh": {"integration"} | has("planning"),
    "compose.yaml": {"agent", "integration"} | has("planning") | has("boundary_model"),
    "docs/assistant-integration.md": set(),
    "README.md": set(),
}
if "planning" in JOBS:
    CASES |= {
        "contracts/planning-v1/ownership.v1.json": {"api", "web", "integration", "planning"},
        "packages/field-shift-models/src/x.py": {"planning"},
        "packages/field-shift-contracts/src/x.py": {"api", "ml", "planning", "integration"},
        "packages/field-shift-science/src/x.py": {"api", "ml", "planning", "integration"},
        "services/crop-model/crop_runtime/x.py": {"planning"},
    }
if "boundary_model" in JOBS:
    CASES |= {
        "services/boundary-model/serve.py": {"boundary_model"},
        "compose.boundary-model.yaml": {"boundary_model"},
        "apps/web/public/bondurant-usgs-aerial.webp": {"boundary_model", "web"} | has("planning"),
    }


@pytest.mark.parametrize("path", sorted(CASES))
def test_each_change_runs_exactly_the_jobs_it_can_break(path):
    assert jobs_for(path) == CASES[path]


def test_a_workflow_change_runs_every_job():
    assert jobs_for(".github/workflows/ci.yml") == set(JOBS)


def test_dropping_any_rule_changes_the_routing_of_a_tested_path():
    # Built-in mutation check: every rule is load-bearing for at least one case above, so deleting
    # or breaking a rule cannot go unnoticed.
    paths = sorted(CASES) + [".github/workflows/ci.yml"]
    baseline = {path: _evaluate(RULES, [path]) for path in paths}
    for index, (*_, line) in enumerate(RULES):
        without = RULES[:index] + RULES[index + 1 :]
        assert any(_evaluate(without, [path]) != baseline[path] for path in paths), (
            f"untested rule: {line}"
        )
