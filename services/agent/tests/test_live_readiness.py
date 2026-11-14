"""M6A: the live Claude smoke suite is wired correctly — tested offline, without spending money.

These tests do NOT make a live call and prove nothing about Claude's behaviour; they prove that the
suite selects the six required cases, stays within its limits, records usage, and refuses to run (and
to fake a result) without credentials.
"""

import asyncio

import pytest

from agent_service.llm.scripted import ScriptedProvider
from evals.cases import CASES
from evals.harness import KnowledgeMissing, run_case
from evals.run import SMOKE, SMOKE_LIMITS, UsageTap, main

BY_ID = {c.id: c for c in CASES}


def test_smoke_suite_covers_the_six_required_behaviours_and_all_are_live_cases():
    assert len(SMOKE) == 6 and all(BY_ID[i].live for i in SMOKE)
    assert SMOKE_LIMITS.max_rounds <= 4 and SMOKE_LIMITS.max_output_tokens <= 2048 and SMOKE_LIMITS.max_tool_calls <= 6


@pytest.mark.parametrize("case_id", SMOKE)
def test_smoke_cases_pass_with_the_scripted_model_under_the_smoke_limits(case_id):
    case = BY_ID[case_id]
    tap = UsageTap(ScriptedProvider(case.script))
    try:
        run, failures = asyncio.run(run_case(case, provider=tap, limits=SMOKE_LIMITS))
    except KnowledgeMissing:
        pytest.skip("knowledge index not built")
    assert failures == []
    assert tap.calls and all(c["in"] >= 0 and c["out"] >= 0 and c["ms"] >= 0 for c in tap.calls)
    assert len(tap.calls) <= SMOKE_LIMITS.max_rounds


def test_live_run_refuses_without_credentials_and_writes_nothing_fake(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("sys.argv", ["evals.run", "--provider", "anthropic", "--smoke"])
    monkeypatch.setattr("evals.run.REPORTS", tmp_path)
    assert main() == 3
    assert list(tmp_path.iterdir()) == []
