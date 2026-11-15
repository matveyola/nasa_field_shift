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
