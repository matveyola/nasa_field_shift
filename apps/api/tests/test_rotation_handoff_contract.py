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


def test_seasonal_source_percentage_is_optional_and_never_derived():
    from field_shift.modules.rotations.handoff.schemas import SeasonalWaterRow

    values = dict(
        job_id="j",
        scenario_id="s",
        plan_id="p",
        crop_id="maize_grain",
        start_date="2027-05-01",
        end_date="2027-09-01",
        data_kind="hybrid_scenario",
        evidence_sha256="e",
        demand_mm=100,
        deficit_mm=20,
    )
    assert SeasonalWaterRow(**values).water_shortfall_pct is None
    row = SeasonalWaterRow(
        **values, water_shortfall_pct=19.98, input_sha256="i", engine_version="v"
    )
    assert row.water_shortfall_pct == 19.98 and row.input_sha256 == "i"
