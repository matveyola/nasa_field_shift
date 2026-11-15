from collections.abc import Sequence
from datetime import date
from math import fsum
from typing import Literal

from pydantic import BaseModel

from field_shift.modules.rotations.waterlogging import WaterloggingEstimate
from field_shift.modules.simulation.schemas import WaterDay


class WaterManagementSummary(BaseModel):
    applied_irrigation_mm: float
    modeled_drainage_mm: float
    drainage_days: int
    max_consecutive_drainage_days: int
    drainage_method: Literal["field_capacity_overflow"] = "field_capacity_overflow"
    waterlogging_stress_days: int | None = None
    waterlogging: WaterloggingEstimate | None = None
    waterlogging_profile_incompatible: bool = False


def summarize_water_management(days: Sequence[WaterDay]) -> WaterManagementSummary:
    """Summarize modeled daily overflow without inferring saturation duration."""
    drainage_days = longest = current = 0
    previous: date | None = None
    for day in days:
        if day.drainage_mm <= 0:
            continue
        drainage_days += 1
        current = current + 1 if previous and (day.date - previous).days == 1 else 1
        longest = max(longest, current)
        previous = day.date

    return WaterManagementSummary(
        applied_irrigation_mm=round(fsum(day.irrigation_mm for day in days), 4),
        modeled_drainage_mm=round(fsum(day.drainage_mm for day in days), 4),
        drainage_days=drainage_days,
        max_consecutive_drainage_days=longest,
    )
