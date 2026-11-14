"""M6A/M6B: the teammate-facing contract checker gives actionable, safe output and never counts SKIPPED or
BLOCKED as passed, nor a stub as a verified teammate integration."""

import asyncio
import json
from argparse import Namespace

import httpx2
import pytest

from agent_service.integrations.contract_check import examples, render, run
from contracts.integration import ComparisonRequest, FieldProfileRequest
from integration_stub.app import create_app


def check(faults: dict | None = None, geo: bool = True, sim: bool = True, token_env: str | None = None,
          partial_field: str | None = None, stub_token: str | None = None, transport=None):
    transport = transport or httpx2.ASGITransport(app=create_app(faults=faults or {}, token=stub_token))
    args = Namespace(geo="http://stub/geo" if geo else None, sim="http://stub/sim" if sim else None, field="demo-poltava-01",
                     revision=3, context=None, token_env=token_env, timeout=2.0, partial_field=partial_field)
    report = asyncio.run(run(args, transport=transport))
    return report, render(report)


def statuses(report) -> dict[str, str]:
    return {s.operation: s.status for s in report.steps}


def test_all_applicable_checks_pass_against_the_stub_and_say_it_is_a_stub():
    report, text = check(partial_field="demo-mykolaiv-01")
    assert report.failed == 0
    assert report.counts() == {"PASS": 11, "BLOCKED_NO_CREDENTIALS": 2, "SKIPPED": 3}
    assert report.target == {"geo": "contract stub (mode stub): NOT a teammate service",
                             "simulation": "contract stub (mode stub): NOT a teammate service"}
    # Passing every check against the stub must never read as a verified teammate integration.
    assert report.verdict().startswith("REAL INTEGRATION: NOT VERIFIED") and "stub" in report.verdict()
    assert text.isascii()  # readable in a Windows console


def test_skipped_and_blocked_are_never_counted_as_passed():
    report, text = check()  # no --partial-field, no --token-env
    s = statuses(report)
    assert s["geo partial profile (unavailable datasets have no value)"] == "SKIPPED"
    assert s["geo: refuses calls without the service token"] == "BLOCKED_NO_CREDENTIALS"
    assert "PASS 10" in text and "SKIPPED 4" in text and "BLOCKED_NO_CREDENTIALS 2" in text


def test_a_service_not_given_is_reported_as_not_performed():
    report, text = check(geo=False)
    assert report.target["geo"].startswith("verification not performed")
    assert "NOT_PERFORMED" in text and "geo GET" not in text


def test_an_unreachable_service_blocks_the_dependent_checks_instead_of_failing_each():
    def refuse(request):
        raise httpx2.ConnectError("connection refused", request=request)
    report, text = check(transport=httpx2.MockTransport(refuse))
    s = statuses(report)
    assert s["geo GET /v1/capabilities"] == "BLOCKED_NO_SERVICE"
    assert s["simulation POST /v1/comparisons"] == "BLOCKED_NO_SERVICE"
    assert "FAIL" not in report.counts() and report.failed > 0  # blocked still makes the run fail
    assert report.verdict() == "REAL INTEGRATION: NOT VERIFIED (no service answered)"


def test_authentication_is_checked_when_a_token_is_given(monkeypatch):
    monkeypatch.setenv("CC_TEST_TOKEN", "stub-token-for-tests-0123456789")
    report, _ = check(token_env="CC_TEST_TOKEN", stub_token="stub-token-for-tests-0123456789")
    assert statuses(report)["geo: refuses calls without the service token"] == "PASS"
    assert report.failed == 0

    # A service that answers anonymous calls fails the check.
    report, text = check(token_env="CC_TEST_TOKEN", stub_token=None)
    step = next(s for s in report.steps if s.operation == "geo: refuses calls without the service token")
    assert step.status == "FAIL" and step.category == "unauthenticated" and "Require the server-to-server" in text


def test_a_wrong_token_is_a_failure_not_a_pass(monkeypatch):
    monkeypatch.setenv("CC_TEST_TOKEN", "not-the-right-token-0123456789")
    report, _ = check(token_env="CC_TEST_TOKEN", stub_token="stub-token-for-tests-0123456789")
    assert statuses(report)["geo GET /v1/capabilities"] == "FAIL"


def test_contract_violations_name_the_field_the_expectation_and_the_fix():
    report, text = check({"sim": "schema"})
    failed = [s for s in report.steps if s.status == "FAIL"]
    assert failed[0].operation == "simulation GET /v1/capabilities" and failed[0].category == "invalid_response"
    assert any(path == "service" for path, _ in failed[0].issues)
    assert "fix: " in text and "fieldshift-integration.schema.json" in text


@pytest.mark.parametrize("fault, category, hint", [
    ("wrong_revision", "wrong_revision", "Echo comparison.field_context_revision"),
    ("mislabel_live", "invalid_response", "data_status 'fixture'"),
    ("expired", "expired", "expires_at must be in the future"),
    ("old_contract", "unsupported_contract", "contract_version"),
])
def test_each_failure_category_comes_with_remediation(fault, category, hint):
    report, text = check({"sim": fault}, geo=False)
    failed = [s for s in report.steps if s.status == "FAIL"]
    assert failed and failed[0].category == category
    assert hint in text


def test_geo_boundary_rule_failures_list_the_dropped_items():
    report, text = check({"geo": "swapped_centroid"}, sim=False)
    failed = [s for s in report.steps if s.status == "FAIL"]
    assert failed[0].operation.startswith("geo POST /v1/field-profile") and "[lon, lat]" in text


def test_the_report_never_contains_the_token(monkeypatch):
    monkeypatch.setenv("CC_TEST_TOKEN", "super-secret-token-value")
    _, text = check({"sim": "unavailable"}, token_env="CC_TEST_TOKEN")
    assert "super-secret" not in text


def test_examples_are_contract_valid_and_carry_no_real_token():
    text = examples()
    blocks = [b.split("\n", 1)[1] for b in text.split("\n\n") if b.startswith("# POST")]
    FieldProfileRequest.model_validate_json(blocks[0])
    ComparisonRequest.model_validate_json(blocks[1])
    assert "<service token from config>" in text and json.loads(text.split("\n", 1)[1].split("\n\n")[0])
