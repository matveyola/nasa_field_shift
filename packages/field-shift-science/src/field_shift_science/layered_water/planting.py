"""Explicit dated calendars replace the research model's Iowa-specific phenology.

Calendar phases and dormancy are supplied by a reviewed regional parameter source.
This module does not infer flowering, local cultivar adaptation or sowing dates.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from types import SimpleNamespace


def days_between(start: date, end: date):
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


@dataclass(frozen=True)
class CalendarParameters:
    crop_id: str
    sowing: date
    harvest: date
    stage_lengths: tuple[int, int, int, int]
    kc_initial: float
    kc_mid: float
    kc_end: float
    root_initial_m: float
    root_max_m: float
    depletion_fraction: float
    height_m: float
    residue_cover: float
    cover_class: str
    dormancy: tuple[tuple[date, date], ...]
    source: str

    def __post_init__(self):
        import math

        if not self.source.strip() or not self.crop_id.strip():
            raise ValueError("Crop calendar and parameter provenance required")
        if self.harvest <= self.sowing or (self.harvest - self.sowing).days > 730:
            raise ValueError("Calendar must be ordered and bounded to two years")
        values = (
            self.kc_initial,
            self.kc_mid,
            self.kc_end,
            self.root_initial_m,
            self.root_max_m,
            self.depletion_fraction,
            self.height_m,
            self.residue_cover,
        )
        if any(not math.isfinite(v) for v in values):
            raise ValueError("Finite crop parameters required")
        if not all(0 <= v <= 3 for v in values[:3]):
            raise ValueError("Crop coefficients outside declared physical bounds")
        if not 0 < self.root_initial_m <= self.root_max_m <= 10:
            raise ValueError("Positive ordered root depths required")
        if not 0 < self.depletion_fraction < 1 or not 0 <= self.residue_cover <= 1:
            raise ValueError("Invalid depletion or residue fraction")
        if not 0 < self.height_m <= 10 or self.cover_class not in {"row", "small_grain"}:
            raise ValueError("Invalid crop height or runoff cover class")
        if len(self.stage_lengths) != 4 or any(
            type(n) is not int or n < 1 for n in self.stage_lengths
        ):
            raise ValueError("Four positive integer phase lengths required")
        dormant = set()
        for start, end in self.dormancy:
            if start > end or start < self.sowing or end > self.harvest:
                raise ValueError("Dormancy window outside crop calendar")
            overlap = set(days_between(start, end))
            if dormant & overlap:
                raise ValueError("Overlapping dormancy windows")
            dormant.update(overlap)
        active = (self.harvest - self.sowing).days + 1 - len(dormant)
        if sum(self.stage_lengths) != active:
            raise ValueError("Phase lengths must equal inclusive active calendar days")


class Planting:
    def __init__(self, parameters: CalendarParameters):
        self.parameters = parameters
        self.start, self.end = parameters.sowing, parameters.harvest

        def value(v):
            return SimpleNamespace(value=v)

        self.crop = SimpleNamespace(
            key=parameters.crop_id,
            kc_mid=value(parameters.kc_mid),
            kc_end=value(parameters.kc_end),
            height_m=value(parameters.height_m),
            p=value(parameters.depletion_fraction),
            residue_cover=value(parameters.residue_cover),
            cover_class=value(parameters.cover_class),
        )
        dormant = {d for start, end in parameters.dormancy for d in days_between(start, end)}
        self._active = {
            d: i
            for i, d in enumerate(d for d in days_between(self.start, self.end) if d not in dormant)
        }
        self._dormant = dormant

    def covers(self, day: date) -> bool:
        return self.start <= day <= self.end

    def stage(self, day: date) -> str:
        if day in self._dormant:
            return "dormant"
        index = self._active[day]
        total = 0
        for stage, length in zip(
            ("ini", "dev", "mid", "late"), self.parameters.stage_lengths, strict=True
        ):
            total += length
            if index < total:
                return stage
        raise ValueError("Day is outside calendar phases")

    def kc_table(self, day: date, kc_mid: float, kc_end: float) -> float:
        p = self.parameters
        if day in self._dormant:
            return p.kc_initial
        index = self._active[day]
        initial, development, mid, late = p.stage_lengths
        if index < initial:
            return p.kc_initial
        if index < initial + development:
            return p.kc_initial + (kc_mid - p.kc_initial) * (index - initial + 1) / development
        if index < initial + development + mid:
            return kc_mid
        return kc_mid + (kc_end - kc_mid) * (index - initial - development - mid + 1) / late

    def root_depth(self, day: date) -> float:
        p = self.parameters
        if day in self._dormant:
            previous = [i for d, i in self._active.items() if d < day]
            index = max(previous, default=0)
        else:
            index = self._active[day]
        fraction = min(index / max(sum(p.stage_lengths[:2]) - 1, 1), 1.0)
        return p.root_initial_m + (p.root_max_m - p.root_initial_m) * fraction
