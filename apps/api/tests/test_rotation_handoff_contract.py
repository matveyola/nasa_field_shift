import pytest
from pydantic import ValidationError

from field_shift.modules.rotations.handoff.schemas import HandoffSubmission


def test_handoff_request_preserves_explicit_open_data_choice_and_native_ids():
    request = HandoffSubmission(
        request_id="farm-plan-1",
        geometry_revision=3,
        operation="forecast",
        parameters={"plans": [{"id": "p1", "crops": [{"crop_id": "wheat_winter_frozen"}]}]},
    )
    assert request.allow_public_data is False
    assert request.parameters["plans"][0]["crops"][0]["crop_id"] == "wheat_winter_frozen"


@pytest.mark.parametrize("key", ["field", "field_id", "as_of", "evidence", "engine", "ruslan_dir"])
def test_client_cannot_override_bound_field_or_source_identity(key):
    with pytest.raises(ValidationError, match="server-owned"):
        HandoffSubmission(
            request_id="a", geometry_revision=0, operation="forecast", parameters={key: "other"}
        )


@pytest.mark.parametrize("parameters", [{"large": "a" * 65536}, {"value": float("nan")}])
def test_parameters_are_finite_and_bounded(parameters):
    with pytest.raises(ValidationError):
        HandoffSubmission(
            request_id="a", geometry_revision=0, operation="forecast", parameters=parameters
        )


def test_unsourced_custom_forecast_calendar_is_not_presented_as_reviewed():
    with pytest.raises(ValidationError, match="sourced catalog"):
        HandoffSubmission(
            request_id="a",
            geometry_revision=0,
            operation="forecast",
            parameters={
                "plans": [
                    {"crops": [{"crop_id": "rapeseed_canola", "stage_lengths": [20, 30, 40, 10]}]}
                ]
            },
        )


def test_malformed_nested_request_reaches_worker_validation_without_type_error():
    # The public contract bounds the opaque native payload; the worker applies its native schema.
    request = HandoffSubmission(
        request_id="a",
        geometry_revision=0,
        operation="forecast",
        parameters={"plans": [{"crops": 42}]},
    )
    assert request.operation == "forecast"


def test_result_defaults_never_authorize_a_rank_without_explicit_interpretation():
    from field_shift.modules.rotations.handoff.schemas import HandoffResult

    result = HandoffResult(
        operation="forecast",
        status="available",
        data_kind="hybrid_scenario",
        engine_version="test",
        source_commit="test",
        input_sha256="a" * 64,
        evidence_sha256="b" * 64,
        acquired_at="2026-01-01T00:00:00Z",
        limitations=[],
    )
    assert not result.ranking_usable and result.suitability_status == "not_assessed"
