from itertools import chain, permutations, product

from field_shift.modules.onboarding.schemas import DemoCrop, DemoRotationPlan
from field_shift.modules.rotations.planning_schemas import Crop, PlanningPriority


def build_demo_plan(
    allowed_crops: list[Crop],
    priority: PlanningPriority,
    start_season: int,
    preceding: Crop | None,
) -> DemoRotationPlan:
    # An illustrative ordering of the farmer's crops, not an agronomic model or forecast.
    ordering = {
        "water": ("Wheat", "Barley", "Peas", "Soy", "Rapeseed", "Sunflower", "Maize"),
        "soil": ("Soy", "Peas", "Wheat", "Barley", "Rapeseed", "Sunflower", "Maize"),
        "low_change": tuple(allowed_crops),
    }[priority]
    allowed = [crop for crop in ordering if crop in allowed_crops]
    previous = preceding
    crops = []
    for offset in range(3):
        # Cycle through selected crops; avoid repeating the immediately preceding crop.
        choices = allowed[offset % len(allowed) :] + allowed[: offset % len(allowed)]
        chosen = next(crop for crop in choices if crop != previous)
        crops.append(DemoCrop(year=start_season + offset, crop=chosen))
        previous = chosen
    alternatives = []
    seen = {tuple(crop.crop for crop in crops)}
    # Prefer permutations containing three different selected crops, then alternating
    # sequences (including the two-crop case). The seven-crop catalogue bounds this search.
    for sequence in chain(permutations(allowed, 3), product(allowed, repeat=3)):
        if (
            sequence in seen
            or sequence[0] == preceding
            or any(a == b for a, b in zip(sequence, sequence[1:]))
        ):
            continue
        alternatives.append(
            [
                DemoCrop(year=start_season + offset, crop=crop)
                for offset, crop in enumerate(sequence)
            ]
        )
        seen.add(sequence)
        if len(alternatives) == 2:
            break
    return DemoRotationPlan(
        start_season=start_season,
        crops=crops,
        alternatives=alternatives,
        explanation=(
            "Illustrative three-season sequence using your selected crops and priority. "
            "It avoids an immediate repeat of the last reported crop."
        ),
        limitations=[
            "Demo plan: no yield, water-risk or soil-health prediction was calculated.",
            "Local suitability and agronomic return intervals have not been validated.",
            "Unknown crop history remains unknown; review this example before farm decisions.",
        ],
    )
