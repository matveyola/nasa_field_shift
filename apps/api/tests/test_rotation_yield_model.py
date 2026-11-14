import pytest
from pydantic import ValidationError

from field_shift.modules.rotations.yield_model import (
    RotationYieldModel,
    artifact_version,
    get_rotation_yield_model,
)


def test_packaged_rotation_model_has_untouched_farms_and_preserves_release_evidence():
    model = get_rotation_yield_model()
    assert model is not None
    data = model.validation
    fitting, calibration, test = map(
        set, (data["fitting_farms"], data["calibration_farms"], data["test_farms"])
    )
    assert not (fitting & calibration or fitting & test or calibration & test)
    assert len(test) >= 3
    assert data["minimum_split_distance_km"] >= 50
    assert model.promoted == all(data["gate"].values())

    candidate = model.model_dump(mode="json", exclude={"version"})
    candidate["promoted"] = True
    candidate["validation"]["gate"]["mae_improvement"] = False
    with pytest.raises(ValidationError, match="no passing release evidence"):
        RotationYieldModel.model_validate({"version": artifact_version(candidate), **candidate})


def test_rotation_effect_is_available_for_supported_maize_transition():
    model = get_rotation_yield_model()
    assert model is not None
    coordinates = {"latitude": 41.705785, "longitude": -93.409033}
    result = model.estimate(
        **coordinates, crop="Maize", preceding_crop="Soybean", crop_sequence=("Soy", "Corn", "Soy")
    )
    assert result is not None
    assert result.lower_t_ha < result.delta_t_ha < result.upper_t_ha
    reference = model.estimate(
        **coordinates, crop="Corn", preceding_crop="Maize", crop_sequence=("Corn",) * 3
    )
    assert reference is not None
    assert reference.delta_t_ha == reference.lower_t_ha == reference.upper_t_ha == 0

    assert (
        model.estimate(
            **coordinates,
            crop="Maize",
            preceding_crop="Maize",
            crop_sequence=("Maize", "Maize", "Soy"),
        )
        is None
    )
    assert (
        model.estimate(
            **coordinates, crop="Soy", preceding_crop="Maize", crop_sequence=("Soy", "Corn", "Soy")
        )
        is None
    )
    assert (
        model.estimate(
            latitude=49,
            longitude=32,
            crop="Maize",
            preceding_crop="Soy",
            crop_sequence=("Maize", "Soy"),
        )
        is None
    )
