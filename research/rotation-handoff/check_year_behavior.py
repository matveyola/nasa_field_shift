"""Check that a crop's estimates depend on its inputs and not the year label."""

from field_shift.modules.rotations.assessment import (
    AssessmentRequest,
    CropSeason,
    _assess_season,
    assess,
)
from field_shift.modules.rotations.historical_fields import (
    FieldDataset,
    SeasonData,
    load_fields,
)

YEAR_SHIFT = 2
CROPS = ("maize_grain", "sunflower")
ROTATIONS = ("marsden-a", "marsden-b")


def move_season(season: SeasonData, year: int) -> SeasonData:
    days = tuple(
        day.model_copy(update={"date": day.date.replace(year=year)})
        for day in season.days
    )
    return season.model_copy(update={"days": days})


def comparable_season(season: CropSeason) -> dict:
    values = season.model_dump(
        exclude={"year", "planting_date", "flowering_start", "flowering_end"}
    )
    for name in ("flowering_start", "flowering_end"):
        event = getattr(season, name)
        values[name] = (event - season.planting_date).days if event else None
    return values


def check_calendar_shift(field: FieldDataset) -> int:
    shifted = field.model_copy(
        update={
            "seasons": {
                year + YEAR_SHIFT: move_season(season, year + YEAR_SHIFT)
                for year, season in field.seasons.items()
            }
        }
    )
    shifted = FieldDataset.model_validate(shifted.model_dump())
    for year in sorted(field.seasons):
        for crop in CROPS:
            original, *original_totals = _assess_season(field, crop, year)
            replay, *replay_totals = _assess_season(shifted, crop, year + YEAR_SHIFT)
            if (
                original_totals != replay_totals
                or comparable_season(original) != comparable_season(replay)
            ):
                raise AssertionError(f"{field.id} {year} {crop} changed after shifting dates.")

    request = AssessmentRequest(
        field_id=field.id,
        rotation_ids=list(ROTATIONS),
        scenarios=["baseline", "drought", "heat"],
    )
    original = assess(request, field)
    replay = assess(request, shifted)
    if original.projection != replay.projection:
        raise AssertionError(f"{field.id} historical range changed after shifting dates.")
    for base, moved in zip(original.results, replay.results, strict=True):
        base_scenario, moved_scenario = base.scenario, moved.scenario
        year_fields = {"analog_year", "replay_years"}
        if (
            base_scenario.model_dump(exclude=year_fields)
            != moved_scenario.model_dump(exclude=year_fields)
            or base_scenario.analog_year + YEAR_SHIFT != moved_scenario.analog_year
            or tuple(year + YEAR_SHIFT for year in base_scenario.replay_years)
            != moved_scenario.replay_years
        ):
            raise AssertionError(f"{field.id} scenario changed after shifting dates.")
        for base_plan, moved_plan in zip(base.rotations, moved.rotations, strict=True):
            if base_plan.model_dump(exclude={"crop_seasons"}) != moved_plan.model_dump(
                exclude={"crop_seasons"}
            ):
                raise AssertionError(f"{field.id} rotation changed after shifting dates.")
            for base_crop, moved_crop in zip(
                base_plan.crop_seasons, moved_plan.crop_seasons, strict=True
            ):
                if (
                    base_crop.year + YEAR_SHIFT != moved_crop.year
                    or comparable_season(base_crop) != comparable_season(moved_crop)
                ):
                    raise AssertionError(
                        f"{field.id} rotation crop changed after shifting dates."
                    )
    return len(field.seasons) * len(CROPS)


def check_weather_response(field: FieldDataset) -> None:
    reference = field.seasons[2022]
    alternate_weather = move_season(field.seasons[2023], 2022).days
    altered = field.model_copy(
        update={
            "seasons": {
                **field.seasons,
                2022: reference.model_copy(update={"days": alternate_weather}),
            }
        }
    )
    for crop in CROPS:
        old, *_ = _assess_season(field, crop, 2022)
        new, *_ = _assess_season(altered, crop, 2022)
        before, after = old.water_shortfall.value_pct, new.water_shortfall.value_pct
        if before == after:
            raise AssertionError(
                f"Marsden {crop} did not respond to the alternate weather."
            )
        print(
            f"Marsden {crop}, 2022 vs 2023 weather, same opening water: "
            f"{before}% -> {after}%"
        )


def main() -> None:
    fields = load_fields()
    bundled = ("marsden_demo", "sutherland_nw", "crawfordsville_se")
    count = sum(check_calendar_shift(fields[field_id]) for field_id in bundled)
    print(
        f"OK: {count} crop-seasons and both rotations match "
        "after moving weather to other years."
    )
    check_weather_response(fields["marsden_demo"])


if __name__ == "__main__":
    main()
