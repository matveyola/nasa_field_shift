"""Reference parameter coverage does not establish local crop suitability."""

import pytest
from field_shift_science.crops.fao56 import crop_water_parameters


@pytest.mark.parametrize(
    ("crop", "kc", "depth", "depletion"),
    [
        ("Maize", 1.20, 1.0, 0.55),
        ("Soy", 1.15, 0.6, 0.50),
        ("Wheat", 1.15, 1.0, 0.55),
        ("Barley", 1.15, 1.0, 0.55),
        ("Sunflower", 1.15, 0.8, 0.45),
        ("Rapeseed", 1.15, 1.0, 0.60),
        ("Peas", 1.15, 0.6, 0.40),
    ],
)
def test_allocation_crops_have_tabulated_fao56_reference(crop, kc, depth, depletion):
    parameters = crop_water_parameters(crop)
    assert parameters is not None
    assert parameters.midseason_coefficient == kc
    assert parameters.root_depth_m == depth
    assert parameters.depletion_fraction == depletion
    # A finite positive extraction threshold bounded by total available water.
    available_mm = 1000 * (0.30 - 0.12) * parameters.root_depth_m
    assert 0 < available_mm * parameters.depletion_fraction < available_mm


@pytest.mark.parametrize("crop", ["Rice", "Fresh peas", "Winter wheat", "", "Unknown"])
def test_unknown_or_distinct_management_profiles_are_not_guessed(crop):
    assert crop_water_parameters(crop) is None


def test_reference_aliases_are_case_and_whitespace_insensitive():
    assert crop_water_parameters(" CANOLA ") == crop_water_parameters("Rapeseed")
    assert crop_water_parameters("dry peas") == crop_water_parameters("Peas")


@pytest.mark.parametrize("crop", ["Barley", "Sunflower", "Rapeseed", "Peas"])
def test_added_crops_use_physical_forecast_without_borrowing_maize_correction(crop, monkeypatch):
    from datetime import UTC, date, datetime

    from field_shift_science.fields.schemas import Polygon
    from field_shift_science.simulation.root_zone_inputs import RootZoneRun, RootZoneWeatherDay

    from field_shift.modules.simulation import forecast

    run = RootZoneRun(
        date(2026, 8, 7),
        50.0,
        30.0,
        "50R1",
        (0.22, 0.24, 0.25),
        (RootZoneWeatherDay(date(2026, 8, 8), 1, 0, 4, 19, 17, (0.21, 0.23, 0.24)),),
    )
    monkeypatch.setattr(forecast, "fetch_ifs_root_zone", lambda *_args: run)

    def forbidden_correction():
        raise AssertionError("A maize-only correction cannot cover another crop")

    monkeypatch.setattr(forecast, "active_root_zone_correction", forbidden_correction)
    result = forecast.run_water_forecast(
        "field",
        crop,
        Polygon(type="Polygon", coordinates=[[(30, 50), (30.01, 50), (30.01, 50.01), (30, 50)]]),
        datetime(2026, 8, 8, tzinfo=UTC),
    )
    day = result.days[0]
    assert result.crop == crop and result.method == "fao56"
    assert result.model_version is None and day.root_zone_estimate_mm is None
    assert day.reserve_mm == pytest.approx(
        day.opening_mm + day.rain_mm - day.water_use_mm - day.drainage_mm
    )
    assert 0 <= day.reserve_mm <= result.capacity_mm
    assert any("reference loam" in limitation for limitation in result.limitations)
