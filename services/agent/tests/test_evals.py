"""The offline eval suite as tests: every case through the real orchestration path."""

import pytest
from evals.cases import CASES
from evals.harness import KnowledgeMissing, run_case
from evals.run import report


@pytest.mark.anyio
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_eval_case(case):
    try:
        _run, failures = await run_case(case)
    except KnowledgeMissing as e:
        pytest.skip(f"knowledge index not built: {e}")
    assert not failures, "; ".join(f"{check}: {reason}" for check, reason in failures)


def test_case_ids_unique_and_categories_covered():
    ids = [c.id for c in CASES]
    assert len(ids) == len(set(ids))
    # "domain" (Task 03, 2026-11-14): farming scope, unrelated requests, follow-ups (evals/domain_cases.py).
    assert {c.category for c in CASES} == {
        "tool_selection",
        "constraints",
        "data_reliability",
        "security",
        "provider",
        "knowledge",
        "integration",
        "domain",
    }
    live = [c.id for c in CASES if c.live]
    assert {
        "ts_general_question",
        "ts_constraints_uk",
        "ts_map_layer",
        "dr_missing_fact",
    } <= set(live)


def test_report_lists_failures_by_category():
    rows = [
        {
            "id": "a",
            "category": "security",
            "title": "",
            "passed": False,
            "terminal": "error",
            "error": "x/y",
            "tools": [],
            "failures": [{"check": "tools.used", "reason": "nope"}],
            "latency_ms": 1,
            "model": None,
            "requests": 1,
        },
        {
            "id": "b",
            "category": "security",
            "title": "",
            "passed": True,
            "terminal": "turn.completed",
            "error": None,
            "tools": ["get_field_facts"],
            "failures": [],
            "latency_ms": 1,
            "model": "m",
            "requests": 2,
        },
    ]
    text = report(rows, "scripted")
    assert (
        "| security | 1 | 2 |" in text
        and "| tools.used | 1 |" in text
        and "| a | security | FAIL |" in text
    )
