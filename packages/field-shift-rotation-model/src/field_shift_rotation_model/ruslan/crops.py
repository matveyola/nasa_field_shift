"""Task 2: crop reference with a source for every value, and crop phenology.

Status of each value:
  "table"      – copied from the cited table (still verify against the PDF before release);
  "adapted"    – derived from the cited source for the Iowa demo field;
  "assumption" – team assumption; replace with local data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import date, timedelta
from functools import cache
from typing import Any

from field_shift_rotation_model.ruslan.weather import normal_temps

FAO56_T11 = "FAO-56, Table 11 (stage lengths)"
FAO56_T12 = "FAO-56, Table 12 (single Kc)"
FAO56_T22 = "FAO-56, Table 22 (rooting depth Zr, depletion fraction p)"
FAO56_CH8 = "FAO-56, ch. 8 (Zr at sowing 0.15-0.20 m; roots grow to Zr max by start of mid-season)"
FAO33 = "FAO-33 (Doorenbos & Kassam, 1979), Table 24 (Ky)"
NASS_628 = "USDA NASS Agricultural Handbook 628 (2010), Iowa usual planting/harvest dates"
ISU_PMR1009 = "Iowa State Univ. Extension PMR 1009 'Corn Growth and Development' (GDD to silking)"
ISU_PM1945 = "Iowa State Univ. Extension PM 1945 'Soybean Growth and Development'"
SCHLENKER = "Schlenker & Roberts (2009), PNAS 106:15594 (yield-damaging heat thresholds)"
PORTER = "Porter & Gawith (1999), Eur. J. Agron. 10:23 (wheat anthesis heat threshold)"
TR55 = "USDA NRCS TR-55 (1986), Table 2-2b (cover class for runoff)"
TEAM = "Field Shift team assumption"
FAO33_STAGE = "FAO-33 (Doorenbos & Kassam, 1979), Table 24, Ky by growth period"
RAO1988 = "Rao, Sarma & Chander (1988), Agric. Water Manage. 13:25 (multiplicative stage model)"
ISU_GDD = "Iowa State Univ. Extension (GDD 86/50 °F method = base 10 °C, cap 30 °C)"
FAO56_CH11 = "FAO-56, ch. 11 (soil evaporation reduced ~5% per 10% of surface covered by residue)"
PRICE_NOTE = (
    "ASSUMPTION for illustration only; replace with current USDA NASS prices and farm yield history"
)


@dataclass(frozen=True, slots=True)
class Val:
    value: Any
    source: str
    status: str = "table"
    note: str = ""


@dataclass(frozen=True, slots=True)
class CropRecord:
    key: str
    name_en: str
    name_uk: str
    group: Val
    region: Val
    kc_ini: Val
    kc_mid: Val
    kc_end: Val
    height_m: Val
    stages_days: Val  # (initial, development, mid, late) in active (non-dormant) days
    root_min_m: Val
    root_max_m: Val
    p: Val
    ky: Val
    planting: Val  # (month, day) default, plus range in note
    planting_range: Val
    end_event: Val  # "harvest" or "termination"
    flowering: Val  # method description (see flowering_window)
    sensitive_phase: Val
    heat_threshold_c: (
        Val  # daily Tmax above which a day in the sensitive phase counts as heat stress
    )
    legume: Val
    cover_class: Val  # runoff cover class
    winter: Val  # dormancy between dormancy_start and green-up
    cover_crop: Val = field(default_factory=lambda: Val(False, TEAM, "assumption"))
    gdd_base_c: Val = field(default_factory=lambda: Val(10.0, TEAM, "assumption"))
    gdd_cap_c: Val = field(default_factory=lambda: Val(30.0, TEAM, "assumption"))
    ky_stages: Val = field(default_factory=lambda: Val(None, "not applicable"))
    residue_cover: Val = field(default_factory=lambda: Val(0.3, TEAM, "assumption"))
    potential_yield_t_ha: Val = field(default_factory=lambda: Val(None, "not applicable"))
    price_usd_t: Val = field(default_factory=lambda: Val(None, "not applicable"))

    def to_dict(self) -> dict:
        return asdict(self)


DORMANCY_START = (12, 1)
GREEN_UP = (4, 1)
_DORMANCY_NOTE = (
    "Iowa: soil frozen ~Dec-Mar; Kc held at Kc_ini during dormancy "
    "(FAO-56 ch. 6, winter wheat with frozen soils)"
)

CROPS: dict[str, CropRecord] = {
    "maize": CropRecord(
        key="maize",
        name_en="Maize (field corn, grain)",
        name_uk="Кукурудза на зерно",
        group=Val("grain hybrid, relative maturity ~105-112 d", TEAM, "assumption"),
        region=Val("Central Iowa, USA (Bondurant demo fields)", TEAM, "assumption"),
        kc_ini=Val(0.30, FAO56_T12),
        kc_mid=Val(1.20, FAO56_T12, note="adjusted for climate with FAO-56 eq. 62 at run time"),
        kc_end=Val(
            0.35, FAO56_T12, note="0.60 if harvested at high grain moisture; 0.35 if field-dried"
        ),
        height_m=Val(2.0, FAO56_T12),
        stages_days=Val((30, 40, 50, 50), FAO56_T11, note="row 'April, Idaho USA'"),
        root_min_m=Val(0.15, FAO56_CH8),
        root_max_m=Val(
            1.5,
            FAO56_T22,
            "adapted",
            "within 1.0-1.7 m; deep Iowa loams (apps/api uses the 1.0 m lower end)",
        ),
        p=Val(0.55, FAO56_T22, note="adjusted p = p + 0.04 (5 - ETc), FAO-56 Table 22 footnote"),
        ky=Val(1.25, FAO33),
        planting=Val((5, 5), NASS_628, "adapted", "middle of the most active period"),
        planting_range=Val(("04-20", "05-25"), NASS_628, "adapted"),
        end_event=Val("harvest", TEAM, "assumption"),
        flowering=Val(
            {"method": "gdd", "gdd_c": 778.0, "base_c": 10.0, "cap_c": 30.0, "window_days": 14},
            ISU_PMR1009,
            "adapted",
            "~1400 GDD(°F, 86/50) to silking = 778 °C-days; window = silking to blister (R1-R2)",
        ),
        sensitive_phase=Val(
            "flowering window (R1-R2)", FAO33, note="FAO-33: flowering is the most sensitive stage"
        ),
        heat_threshold_c=Val(
            35.0, SCHLENKER, "assumption", "pollen viability; S&R yield threshold is 29 °C"
        ),
        legume=Val(False, "botany"),
        cover_class=Val("row", TR55),
        winter=Val(False, "botany"),
    ),
    "soybean": CropRecord(
        key="soybean",
        name_en="Soybean",
        name_uk="Соя",
        group=Val("maturity group 2-3", TEAM, "assumption"),
        region=Val("Central Iowa, USA (Bondurant demo fields)", TEAM, "assumption"),
        kc_ini=Val(0.40, FAO56_T12),
        kc_mid=Val(1.15, FAO56_T12),
        kc_end=Val(0.50, FAO56_T12),
        height_m=Val(0.75, FAO56_T12, note="table range 0.5-1.0 m"),
        stages_days=Val((20, 35, 60, 25), FAO56_T11, note="row 'May, Central USA'"),
        root_min_m=Val(0.15, FAO56_CH8),
        root_max_m=Val(
            1.3,
            FAO56_T22,
            "adapted",
            (
                "upper end of 0.6-1.3 m for deep Iowa loams (apps/api uses 0.6 "
                "m); stage-Ky soybean yield is very "
                "sensitive: median relative yield 0.48 at 1.0 m vs 0.61 "
                "at 1.3 m on synthetic weather"
            ),
        ),
        p=Val(0.50, FAO56_T22),
        ky=Val(0.85, FAO33),
        planting=Val((5, 15), NASS_628, "adapted"),
        planting_range=Val(("05-01", "06-05"), NASS_628, "adapted"),
        end_event=Val("harvest", TEAM, "assumption"),
        flowering=Val(
            {"method": "dap", "start_dap": 45, "window_days": 20},
            ISU_PM1945,
            "adapted",
            "R1 ~45 days after mid-May planting (photoperiod-driven); window R1-R3",
        ),
        sensitive_phase=Val("R1-R3 flowering and early pod set", FAO33),
        heat_threshold_c=Val(35.0, SCHLENKER, "assumption", "S&R yield threshold is 30 °C"),
        legume=Val(True, "botany"),
        cover_class=Val("row", TR55),
        winter=Val(False, "botany"),
    ),
    "winter_wheat": CropRecord(
        key="winter_wheat",
        name_en="Winter wheat",
        name_uk="Пшениця озима",
        group=Val("soft red winter wheat", TEAM, "assumption"),
        region=Val("Central Iowa, USA (Bondurant demo fields)", TEAM, "assumption"),
        kc_ini=Val(0.40, FAO56_T12, note="'with frozen soils'; 0.7 with non-frozen soils"),
        kc_mid=Val(1.15, FAO56_T12),
        kc_end=Val(0.25, FAO56_T12, note="table 0.25-0.4"),
        height_m=Val(1.0, FAO56_T12),
        stages_days=Val(
            (60, 50, 35, 20),
            FAO56_T11,
            "adapted",
            (
                "active days, dormancy Dec 1-Mar 31 excluded; FAO-56 Table 11 "
                "rows are for other regions"
            ),
        ),
        root_min_m=Val(0.15, FAO56_CH8),
        root_max_m=Val(1.5, FAO56_T22, "adapted", "lower end of 1.5-1.8 m"),
        p=Val(0.55, FAO56_T22),
        ky=Val(1.0, FAO33, note="winter wheat; spring wheat 1.15"),
        planting=Val((10, 1), NASS_628, "adapted"),
        planting_range=Val(("09-20", "10-15"), NASS_628, "adapted"),
        end_event=Val("harvest", TEAM, "assumption"),
        flowering=Val(
            {"method": "calendar", "start": (6, 1), "window_days": 12},
            NASS_628,
            "assumption",
            "anthesis ~early June in central Iowa; replace with GDD model when available",
        ),
        sensitive_phase=Val("anthesis and early grain fill", FAO33),
        heat_threshold_c=Val(31.0, PORTER, "assumption"),
        legume=Val(False, "botany"),
        cover_class=Val("small_grain", TR55),
        winter=Val(True, "botany", note=_DORMANCY_NOTE),
    ),
    "cereal_rye": CropRecord(
        key="cereal_rye",
        name_en="Cereal rye cover crop",
        name_uk="Жито озиме (покривна культура)",
        group=Val("cover crop, terminated before soybean", TEAM, "assumption"),
        region=Val("Central Iowa, USA (Bondurant demo fields)", TEAM, "assumption"),
        kc_ini=Val(
            0.40, FAO56_T12, "adapted", "winter wheat as proxy; FAO-56 has no cereal rye row"
        ),
        kc_mid=Val(
            1.15, FAO56_T12, "adapted", "winter wheat as proxy; not reached before termination"
        ),
        kc_end=Val(0.25, FAO56_T12, "adapted", "winter wheat as proxy"),
        height_m=Val(0.5, TEAM, "assumption", "height at termination"),
        stages_days=Val(
            (37, 50, 35, 20), TEAM, "assumption", "drilled after maize harvest, terminated May 1"
        ),
        root_min_m=Val(0.15, FAO56_CH8),
        root_max_m=Val(1.0, TEAM, "assumption"),
        p=Val(0.55, FAO56_T22, "adapted", "small grains"),
        ky=Val(None, "not harvested"),
        planting=Val((10, 25), TEAM, "assumption"),
        planting_range=Val(("10-15", "11-10"), TEAM, "assumption"),
        end_event=Val(
            "termination", TEAM, "assumption", "terminated ~2 weeks before soybean planting"
        ),
        flowering=Val(None, "terminated before heading"),
        sensitive_phase=Val(None, "not a cash crop"),
        heat_threshold_c=Val(None, "not applicable"),
        legume=Val(False, "botany"),
        cover_class=Val("small_grain", TR55),
        winter=Val(True, "botany", note=_DORMANCY_NOTE),
        cover_crop=Val(True, TEAM, "assumption"),
    ),
}

_EXTRA = {
    "maize": dict(
        gdd_base_c=Val(10.0, ISU_GDD, "adapted"),
        gdd_cap_c=Val(30.0, ISU_GDD, "adapted"),
        ky_stages=Val(
            {"vegetative": 0.4, "flowering": 1.5, "yield_formation": 0.5, "ripening": 0.2},
            FAO33_STAGE,
            note="combined multiplicatively (Rao et al. 1988); verify against the table",
        ),
        residue_cover=Val(0.7, FAO56_CH11, "assumption", "no-till maize stover"),
        potential_yield_t_ha=Val(13.0, TEAM, "assumption", PRICE_NOTE),
        price_usd_t=Val(170.0, TEAM, "assumption", PRICE_NOTE),
    ),
    "soybean": dict(
        gdd_base_c=Val(10.0, TEAM, "assumption"),
        gdd_cap_c=Val(30.0, TEAM, "assumption"),
        ky_stages=Val(
            {"vegetative": 0.2, "flowering": 0.8, "yield_formation": 1.0, "ripening": 0.2},
            FAO33_STAGE,
            note="ripening value is an assumption; verify against the table",
        ),
        residue_cover=Val(0.3, FAO56_CH11, "assumption"),
        potential_yield_t_ha=Val(4.0, TEAM, "assumption", PRICE_NOTE),
        price_usd_t=Val(380.0, TEAM, "assumption", PRICE_NOTE),
    ),
    "winter_wheat": dict(
        gdd_base_c=Val(0.0, TEAM, "assumption", "small grains usually use base 0 °C"),
        gdd_cap_c=Val(30.0, TEAM, "assumption"),
        ky_stages=Val(
            {"vegetative": 0.2, "flowering": 0.6, "yield_formation": 0.5, "ripening": 0.2},
            FAO33_STAGE,
            note="ripening value is an assumption; verify against the table",
        ),
        residue_cover=Val(0.6, FAO56_CH11, "assumption", "straw left on the field"),
        potential_yield_t_ha=Val(5.0, TEAM, "assumption", PRICE_NOTE),
        price_usd_t=Val(210.0, TEAM, "assumption", PRICE_NOTE),
    ),
    "cereal_rye": dict(
        gdd_base_c=Val(0.0, TEAM, "assumption"),
        gdd_cap_c=Val(30.0, TEAM, "assumption"),
        residue_cover=Val(0.5, FAO56_CH11, "assumption", "terminated rye left as mulch"),
    ),
}
CROPS = {k: replace(v, **_EXTRA.get(k, {})) for k, v in CROPS.items()}


def flat_rows() -> list[dict]:
    """One row per (crop, parameter) for the CSV deliverable."""
    rows = []
    for crop in CROPS.values():
        for name, val in crop.to_dict().items():
            if isinstance(val, dict) and "source" in val:
                rows.append(
                    {
                        "crop": crop.key,
                        "parameter": name,
                        "value": val["value"],
                        "source": val["source"],
                        "status": val["status"],
                        "note": val["note"],
                    }
                )
    return rows


# --------------------------------------------------------------------------- phenology
# All stages run on thermal time (growing degree days). Stage boundaries in °C-days are calibrated
# so that, under CLIMATE NORMALS from the default planting date, the FAO-56 stage lengths in days
# are reproduced. In a warmer or cooler season every stage, the root growth and the flowering
# window shift together (fixes the earlier mismatch of GDD flowering vs calendar Kc stages).
# Photoperiod (important for soybean) is ignored.

KILLING_FROST_C = -2.0  # first Tmin <= -2 °C after Sep 1 ends a summer crop (ASSUMPTION)


def _in_dormancy(d: date) -> bool:
    md = (d.month, d.day)
    return md >= DORMANCY_START or md < GREEN_UP


def gdd_c(tmax: float, tmin: float, base: float = 10.0, cap: float = 30.0) -> float:
    """Growing degree days, 86/50 °F method in °C (temperatures clipped to [base, cap])."""
    hi = min(max(tmax, base), cap)
    lo = min(max(tmin, base), cap)
    return (hi + lo) / 2 - base


def _gdd(crop: CropRecord, tmax: float, tmin: float) -> float:
    return gdd_c(tmax, tmin, crop.gdd_base_c.value, crop.gdd_cap_c.value)


def _normal_tt_after(
    crop: CropRecord, start: date, active_days: int | None = None, until: date | None = None
) -> float:
    """Thermal time under normals from `start`, over active days or until a date."""
    tt, n, d = 0.0, 0, start
    while (active_days is not None and n < active_days) or (until is not None and d < until):
        if not (crop.winter.value and _in_dormancy(d)):
            tt += _gdd(crop, *normal_temps(d))
            n += 1
        d += timedelta(days=1)
    return tt


def reset_calibration() -> None:
    """Forget stage/flowering/sowing calibrations (call after fstasks.weather.set_normals)."""
    thermal_stages.cache_clear()
    flowering_tt.cache_clear()
    sowing_threshold_c.cache_clear()


@cache
def thermal_stages(key: str) -> tuple[float, float, float, float]:
    """Cumulative °C-days at the END of the initial, development, mid and late stages."""
    crop = CROPS[key]
    start = date(2001, *crop.planting.value)
    out, acc = [], 0
    for n in crop.stages_days.value:
        acc += n
        out.append(round(_normal_tt_after(crop, start, active_days=acc), 1))
    return tuple(out)


@cache
def flowering_tt(key: str) -> float | None:
    """Thermal time from planting to the start of flowering."""
    crop = CROPS[key]
    rule = crop.flowering.value
    if not rule:
        return None
    if rule["method"] == "gdd":
        return float(rule["gdd_c"])
    start = date(2001, *crop.planting.value)
    if rule["method"] == "dap":
        return round(_normal_tt_after(crop, start, active_days=rule["start_dap"]), 1)
    year = start.year + (1 if crop.winter.value else 0)
    return round(_normal_tt_after(crop, start, until=date(year, *rule["start"])), 1)


@cache
def sowing_threshold_c(key: str) -> float:
    """5-day mean air temperature that, under climate normals, is first reached on the crop's
    default sowing date. Calibrated like the thermal stages, so a normal spring reproduces the
    default date and warm or cold springs move it. (A fixed 10 °C air threshold fired on the first
    day of the range almost every year.)"""
    crop = CROPS[key]
    d = date(2001, *crop.planting.value)
    return round(sum(sum(normal_temps(d - timedelta(days=k))) / 2 for k in range(5)) / 5, 1)


def planting_date(
    crop: CropRecord,
    year: int,
    temps: Mapping[date, tuple[float, float]],
    rain: Mapping[date, float] | None = None,
    not_before: date | None = None,
) -> date:
    """Rule-based sowing date.

    Summer crops: first day inside the planting range when the 5-day mean air temperature reaches
    the crop's sowing threshold (see `sowing_threshold_c`) and that day has < 5 mm rain; the range
    end if the rule never fires. Winter and cover crops: the default date.
    The date is never before `not_before` (e.g. the predecessor's harvest + a few days).
    """
    lo_md, hi_md = (tuple(int(x) for x in v.split("-")) for v in crop.planting_range.value)
    lo, hi = date(year, *lo_md), date(year, *hi_md)
    default = date(year, *crop.planting.value)
    if crop.winter.value:
        return max(default, not_before) if not_before else default
    threshold = sowing_threshold_c(crop.key)
    d = max(lo, not_before) if not_before else lo
    while d <= hi:
        window = [temps.get(d - timedelta(days=k)) for k in range(5)]
        if all(window):
            tmean = sum((a + b) / 2 for a, b in window) / 5
            if tmean >= threshold and (rain is None or rain.get(d, 0.0) < 5.0):
                return d
        d += timedelta(days=1)
    return max(hi, not_before) if not_before else hi


@dataclass(slots=True)
class Planting:
    """One crop on the field from `start` to maturity, a killing frost or a forced end.

    `temps` maps date -> (tmax, tmin) and must cover the dates to be simulated. If it ends before
    maturity, `end` is the last date with temperatures and `complete` is False.
    """

    crop: CropRecord
    start: date
    temps: Mapping[date, tuple[float, float]]
    end: date | None = None  # forced end, e.g. cover-crop termination
    complete: bool = field(default=False, init=False)
    end_reason: str = field(default="", init=False)
    _tt: dict[date, float] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        stages = thermal_stages(self.crop.key)
        forced, tt, d, last = self.end, 0.0, self.start, None
        while d in self.temps:
            if not (self.crop.winter.value and _in_dormancy(d)):
                tt += _gdd(self.crop, *self.temps[d])
            self._tt[d] = tt
            last = d
            if forced is not None and d >= forced:
                self.end, self.complete, self.end_reason = d, True, self.crop.end_event.value
                return
            if tt >= stages[3]:
                self.end, self.complete, self.end_reason = d, True, "maturity"
                return
            if (
                not self.crop.winter.value
                and d.month >= 9
                and d > self.start + timedelta(days=60)
                and self.temps[d][1] <= KILLING_FROST_C
            ):
                self.end, self.complete, self.end_reason = d, True, "killing frost"
                return
            d += timedelta(days=1)
        if last is None:
            raise ValueError(f"no temperatures from {self.start} for {self.crop.key}")
        self.end, self.complete, self.end_reason = last, False, "temperatures end"

    def covers(self, d: date) -> bool:
        return self.start <= d <= self.end

    def thermal(self, d: date) -> float:
        return self._tt[d]

    def stage(self, d: date) -> str:
        if self.crop.winter.value and _in_dormancy(d):
            return "dormant"
        x = self._tt[d]
        t1, t2, t3, _ = thermal_stages(self.crop.key)
        if x <= t1:
            return "ini"
        if x <= t2:
            return "dev"
        if x <= t3:
            return "mid"
        return "late"

    def kc_table(self, d: date, kc_mid: float | None = None, kc_end: float | None = None) -> float:
        """FAO-56 piecewise-linear Kc curve (Fig. 25, eq. 66) on thermal time instead of days."""
        c = self.crop
        k_ini = c.kc_ini.value
        k_mid = c.kc_mid.value if kc_mid is None else kc_mid
        k_end = c.kc_end.value if kc_end is None else kc_end
        if c.winter.value and _in_dormancy(d):
            return k_ini
        x = self._tt[d]
        t1, t2, t3, t4 = thermal_stages(c.key)
        if x <= t1:
            return k_ini
        if x <= t2:
            return k_ini + (k_mid - k_ini) * (x - t1) / (t2 - t1)
        if x <= t3:
            return k_mid
        return k_mid + (k_end - k_mid) * min((x - t3) / (t4 - t3), 1.0)

    def root_depth(self, d: date) -> float:
        """Linear in thermal time from Zr min at planting to Zr max at the start of mid-season."""
        c = self.crop
        frac = min(self._tt[d] / thermal_stages(c.key)[1], 1.0)
        return c.root_min_m.value + (c.root_max_m.value - c.root_min_m.value) * frac


def flowering_window(planting: Planting) -> tuple[date, date] | None:
    """Start = first day the planting's thermal time reaches the crop's flowering threshold."""
    rule = planting.crop.flowering.value
    target = flowering_tt(planting.crop.key)
    if target is None:
        return None
    d = planting.start
    while d <= planting.end:
        if planting.thermal(d) >= target:
            return d, d + timedelta(days=rule["window_days"] - 1)
        d += timedelta(days=1)
    return None
