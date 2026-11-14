from datetime import date

import pytest

from field_shift.modules.simulation.water_balance import (
    DemoWaterModel,
    WaterInputs,
    stress_summary,
)
from field_shift.modules.weather.scenarios import Scenario


def test_dry_maize_tracks_daily_water_and_stress() -> None:
    result = DemoWaterModel().run(WaterInputs("F-03", "Maize", 2026, Scenario.DRY_YEAR))

    assert result.demo
    assert result.threshold_mm == 60
    assert result.first_stress_date == date(2026, 8, 3)
    assert result.min_reserve_mm == 58
    assert result.stress_days == 7
    assert stress_summary(result) == (7, 3)
    assert result.days[0].reserve_mm == 74
    assert result.days[-1].reserve_mm == 64
    for opening, closing in zip(result.days[:-1], result.days[1:], strict=True):
        assert closing.opening_mm == opening.reserve_mm
    for day in result.days:
        assert day.reserve_mm == pytest.approx(
            day.opening_mm + day.rain_mm + day.irrigation_mm - day.water_use_mm - day.drainage_mm
        )


def test_scenarios_use_same_crop_and_reduced_rain_cannot_increase_reserve() -> None:
    model = DemoWaterModel()
    dry = model.run(WaterInputs("F-03", "Maize", 2026, Scenario.DRY_YEAR))
    less_rain = model.run(WaterInputs("F-03", "Maize", 2026, Scenario.LESS_RAIN))
    baseline = model.run(WaterInputs("F-03", "Maize", 2026, Scenario.BASELINE))

    assert dry.flowering_start == less_rain.flowering_start == baseline.flowering_start
    assert all(
        less.reserve_mm <= reference.reserve_mm <= base.reserve_mm
        for less, reference, base in zip(less_rain.days, dry.days, baseline.days, strict=True)
    )


def test_irrigation_is_applied_once_and_excess_drains() -> None:
    result = DemoWaterModel().run(WaterInputs("F-03", "Maize", 2026, Scenario.DRY_YEAR, 100))

    first, *later = result.days
    assert (first.irrigation_mm, first.reserve_mm, first.drainage_mm) == (100, 100, 74)
    assert all(day.irrigation_mm == 0 for day in later)
    assert result.first_stress_date is None
    assert result.stress_days == 0


def test_missing_day_interrupts_contiguous_stress_episode() -> None:
    result = DemoWaterModel().run(WaterInputs("F-03", "Maize", 2026, Scenario.DRY_YEAR))
    missing_day = result.model_copy(update={"days": [*result.days[:5], *result.days[6:]]})

    # Removing Aug 6 leaves Aug 3–5 and Aug 7–9 as separate three-day episodes.
    assert stress_summary(missing_day) == (3, 3)
