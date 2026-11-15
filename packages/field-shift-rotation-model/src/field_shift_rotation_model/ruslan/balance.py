"""Daily root-zone water balance (FAO-56 ch. 8, eq. 85) in STORAGE form, on a layered profile.

Sign convention (one place): storage S = water above wilting point, mm.
FAO-56 depletion is Dr = TAW - S; use `depletion()` if a deficit is needed.

The soil profile is split into 0.1 m layers. Rain infiltrates top-down; water above field
capacity in the lowest layer is deep percolation. Crops draw water only from layers inside
the current root depth Zr. This replaces the FAO-56 shortcut "a new layer reached by roots
is at field capacity" with the water actually tracked in that layer, so the reserve carries
over between crops, fallow and cover crops (task 4).

Stress, as in FAO-56 eq. 84 (written for storage):
  threshold S_thr = (1 - p) TAW,  Ks = 1 if S >= S_thr else S / S_thr,  ETa = Ks Kc ET0.
Ks uses the opening storage of the day (FAO-56 uses Dr of the previous day).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import mean

from field_shift_rotation_model.ruslan.crops import Planting
from field_shift_rotation_model.ruslan.meteo import kc_climate_adjust
from field_shift_rotation_model.ruslan.soil import CURVE_NUMBER_B, SoilHydraulics, runoff_scs
from field_shift_rotation_model.ruslan.weather import WeatherDay

LAYER_M = 0.1
BARE_KE_MAX = 1.05  # FAO-56 eq. 71-72: Ke <= Kc_max (1.05-1.20); lower end used
REW_MM = 9.0  # FAO-56 Table 19, readily evaporable water for loam (8-10 mm)
MODEL_VERSION = "fstasks-layered-bucket-v1"
# In the initial stage Kc_ini is mostly soil evaporation (FAO-56 ch. 6), and dormant crops do not
# transpire, so a dry topsoil there reduces ET (Ks still applies) but is not counted as crop stress.
NO_STRESS_STAGES = ("ini", "dormant")
# Snow and frozen soil (simple degree-day scheme; ASSUMPTIONS, typical literature ranges):
SNOW_TMEAN_C = 0.0  # precipitation falls as snow when daily mean T <= 0 °C
MELT_MM_PER_C_DAY = 3.0  # degree-day melt factor (2-6 mm/°C/day in the literature)
FROZEN_TMEAN_7D_C = -1.0  # soil treated as frozen when the 7-day mean air T is below this
FROZEN_CN = 95  # near-impervious frozen soil
DEFAULT_RESIDUE = 0.3  # residue cover before the first simulated harvest


def depletion(storage_mm: float, taw_mm: float) -> float:
    """Convert storage to FAO-56 root-zone depletion Dr."""
    return taw_mm - storage_mm


def relative_to_storage(relative: float, capacity_mm: float) -> float:
    return min(max(relative, 0.0), 1.0) * capacity_mm


def smap_relative_wetness(sm: float, p01: float, p99: float) -> float:
    """SMAP sm_rootzone (m3/m3) -> relative wetness 0..1 using the 1st/99th percentiles of
    several years at that SMAP cell. Lets the field's own TAW absorb SMAP's model scale
    (SMAP 0-100 cm != the crop root depth; label this approximation in outputs)."""
    if p99 <= p01:
        raise ValueError("p99 must exceed p01")
    return min(max((sm - p01) / (p99 - p01), 0.0), 1.0)


class Profile:
    def __init__(
        self,
        soil: SoilHydraulics,
        depth_m: float = 1.5,
        relative_wetness: float = 0.5,
        layer_awc: Sequence[float] | None = None,
        layer_gravity: Sequence[float] | None = None,
        drain: Sequence[float] | None = None,
    ):
        """`layer_awc` (m3/m3 per 0.1 m layer, top first) gives a layered soil such as an SSURGO
        profile; without it every layer has soil.awc.

        Drainage. Default (drain=None): water above field capacity leaves the layer the same day
        ("tipping bucket", FAO-56). With `layer_gravity`
        (saturation minus field capacity, m3/m3) and
        `drain` (fraction of the water above field capacity that drains per day, per layer, 0-1), a
        layer can hold water up to saturation and passes it down gradually (AquaCrop-style)."""
        self.soil = soil
        self.n = round(depth_m / LAYER_M)
        if layer_awc is not None and len(layer_awc) < self.n:
            raise ValueError(f"layer_awc has {len(layer_awc)} layers, the profile needs {self.n}")
        awc = list(layer_awc[: self.n]) if layer_awc is not None else [soil.awc] * self.n
        self.caps = [1000.0 * a * LAYER_M for a in awc]  # mm per layer, above wilting point
        self.cap = self.caps[0]  # top layer (bare-soil evaporation)
        self.w = [relative_to_storage(relative_wetness, c) for c in self.caps]
        self.drain = None
        if drain is not None:
            if layer_gravity is None:
                raise ValueError(
                    "delayed drainage needs layer_gravity (saturation minus field capacity)"
                )
            pad = lambda v: list(v[: self.n]) + [v[-1]] * (self.n - len(v))  # noqa: E731 deeper layers = deepest given
            self.drain = [min(max(float(k), 0.0), 1.0) for k in pad(drain)]
            self.sat = [
                c + 1000.0 * g * LAYER_M for c, g in zip(self.caps, pad(layer_gravity), strict=True)
            ]
        self.snow_mm = 0.0  # snow water equivalent on the surface
        self.tmean_hist: list[float] = []  # last 7 daily mean air temperatures

    @property
    def depth_m(self) -> float:
        return self.n * LAYER_M

    def _weights(self, z: float) -> list[float]:
        z = min(max(z, 0.0), self.depth_m)
        return [min(max(z / LAYER_M - i, 0.0), 1.0) for i in range(self.n)]

    def storage(self, z: float) -> float:
        return sum(w * f for w, f in zip(self.w, self._weights(z), strict=True))

    def capacity(self, z: float) -> float:
        return sum(c * f for c, f in zip(self.caps, self._weights(z), strict=True))

    def total(self) -> float:
        return sum(self.w)

    def infiltrate(self, water: float) -> float:
        """Fill layers top-down; return deep percolation below the profile."""
        if self.drain is not None:
            return self._infiltrate_delayed(max(water, 0.0))
        for i in range(self.n):
            room = self.caps[i] - self.w[i]
            take = min(room, water)
            self.w[i] += take
            water -= take
            if water <= 0:
                return 0.0
        return water

    def _infiltrate_delayed(self, water: float) -> float:
        """One day: inflow fills each layer up to saturation; a fraction of the water above field
        capacity drains to the layer below (cascading within the day).
        Called every day, also with 0."""
        flow = water
        for i in range(self.n):
            self.w[i] += flow
            over = max(self.w[i] - self.sat[i], 0.0)
            self.w[i] -= over
            d = self.drain[i] * max(self.w[i] - self.caps[i], 0.0)
            self.w[i] -= d
            flow = over + d
        return flow

    def extract(self, amount: float, z: float) -> float:
        """Remove up to `amount` from the root zone, in proportion to water held."""
        weights = [max(w, 0.0) * f for w, f in zip(self.w, self._weights(z), strict=True)]
        held = sum(weights)
        if held <= 0 or amount <= 0:
            return 0.0
        take = min(amount, held)
        for i, wt in enumerate(weights):
            if wt > 0:
                self.w[i] = max(self.w[i] - take * wt / held, 0.0)
        return take

    def evaporate(self, amount: float, floor_mm: float) -> float:
        """Remove soil evaporation from the top layer, down to `floor_mm` below wilting point
        (air-dry soil holds less water than at -1500 kPa)."""
        take = min(max(amount, 0.0), max(self.w[0] + floor_mm, 0.0))
        self.w[0] -= take
        return take


@dataclass(frozen=True, slots=True)
class DayResult:
    date: date
    cover: str
    stage: str
    zr_m: float
    taw_mm: float
    threshold_mm: float | None
    storage_mm: float  # root zone (crop) or top 1.0 m (bare)
    opening_mm: float
    profile_mm: float
    rain_mm: float  # precipitation (rain + snow water equivalent) after any scenario multiplier
    source_rain_mm: float
    runoff_mm: float
    irrigation_mm: float
    et0_mm: float
    kc: float
    etc_mm: float
    eta_mm: float
    drainage_mm: float
    ks: float
    stress: bool
    snow_mm: float
    melt_mm: float
    frozen: bool
    residue_cover: float
    tmax: float
    tmin: float
    source: str
    gap: bool


def _active(plantings: Sequence[Planting], d: date) -> Planting | None:
    for p in plantings:
        if p.covers(d):
            return p
    return None


def _residue(plantings: Sequence[Planting], d: date) -> float:
    ended = [p for p in plantings if p.end < d]
    return max(ended, key=lambda p: p.end).crop.residue_cover.value if ended else DEFAULT_RESIDUE


def _climate_kc(planting: Planting, weather: dict[date, WeatherDay]) -> tuple[float, float]:
    """Kc_mid / Kc_end adjusted with mean u2 and RHmin over the mid and late stages."""
    c = planting.crop
    days = [
        w for d, w in weather.items() if planting.covers(d) and planting.stage(d) in ("mid", "late")
    ]
    u2 = [w.u2 for w in days if w.u2 is not None]
    rh = [w.rhmin for w in days if w.rhmin is not None]
    if not u2 or not rh:
        return c.kc_mid.value, c.kc_end.value
    h = c.height_m.value
    return (
        kc_climate_adjust(c.kc_mid.value, mean(u2), mean(rh), h),
        kc_climate_adjust(c.kc_end.value, mean(u2), mean(rh), h),
    )


def simulate(
    profile: Profile,
    weather: Iterable[WeatherDay],
    plantings: Sequence[Planting],
    *,
    rain_multiplier: float = 1.0,
    irrigation: dict[date, float] | None = None,
    irrigation_capacity_mm: float | None = None,
    runoff: bool = True,
    bare_reference_m: float = 1.0,
    record_layers: list | None = None,
) -> list[DayResult]:
    """Run the balance day by day. Mutates `profile` (so a run can be continued).
    Values are kept at full precision; round only when publishing."""
    days = list(weather)
    wx = {w.date: w for w in days}
    kc_adj = {id(p): _climate_kc(p, wx) for p in plantings}
    if irrigation and irrigation_capacity_mm is not None:
        raise ValueError("Use an irrigation schedule or adaptive irrigation capacity.")
    irrigation = irrigation or {}
    out: list[DayResult] = []
    for w in days:
        if not (w.rain_mm >= 0 and w.et0_mm >= 0):
            raise ValueError(f"invalid weather on {w.date}")
        p = _active(plantings, w.date)
        precip = w.rain_mm * rain_multiplier
        irr = irrigation.get(w.date, 0.0)
        # snow and frozen soil
        tmean = (w.tmax + w.tmin) / 2
        profile.tmean_hist = (profile.tmean_hist + [tmean])[-7:]
        frozen = len(profile.tmean_hist) == 7 and mean(profile.tmean_hist) < FROZEN_TMEAN_7D_C
        if tmean <= SNOW_TMEAN_C:
            profile.snow_mm += precip
            liquid = 0.0
        else:
            liquid = precip
        melt = min(profile.snow_mm, MELT_MM_PER_C_DAY * max(tmean, 0.0))
        profile.snow_mm -= melt
        water_in = liquid + melt
        snow_cover = profile.snow_mm > 0
        residue = _residue(plantings, w.date) if p is None else 0.0
        if frozen:
            cn = FROZEN_CN
        elif p is not None:
            cn = CURVE_NUMBER_B[p.crop.cover_class.value]
        else:
            cn = CURVE_NUMBER_B["residue" if residue >= 0.3 else "bare"]
        q = runoff_scs(water_in, cn) if runoff else 0.0
        if p is not None:
            zr = p.root_depth(w.date)
            taw = profile.capacity(zr)
            opening = profile.storage(zr)
            kc = 0.0 if snow_cover else p.kc_table(w.date, *kc_adj[id(p)])
            etc = kc * w.et0_mm
            pfrac = min(max(p.crop.p.value + 0.04 * (5 - etc), 0.1), 0.8)
            thr = (1 - pfrac) * taw
            effective_rain = water_in - q
            if irrigation_capacity_mm is not None and not frozen and not snow_cover:
                # Refill toward readily available water plus today's crop demand.
                target_storage = min(taw, thr + etc)
                irr = min(
                    irrigation_capacity_mm, max(0.0, target_storage - opening - effective_rain)
                )
            dp = profile.infiltrate(effective_rain + irr)
            available = profile.storage(zr) if irrigation_capacity_mm is not None else opening
            ks = 1.0 if available >= thr else max(available / thr, 0.0)
            eta = profile.extract(ks * etc, zr)
            storage = profile.storage(zr)
            stage, cover = p.stage(w.date), p.crop.key
        else:
            zr, thr, cover, stage = 0.0, None, "fallow", "fallow"
            taw = profile.capacity(bare_reference_m)
            opening = profile.storage(bare_reference_m)
            dp = profile.infiltrate(water_in - q + irr)
            top = profile.w[0]
            kr = (
                1.0
                if top >= profile.cap - REW_MM
                else max(top / max(profile.cap - REW_MM, 1e-9), 0.0)
            )
            # FAO-56 ch. 11: evaporation reduced ~5% per 10% residue cover; none under snow
            kc = 0.0 if snow_cover else BARE_KE_MAX * kr * (1 - 0.5 * residue)
            etc = kc * w.et0_mm
            ks = 1.0
            eta = profile.extract(etc, LAYER_M)
            storage = profile.storage(bare_reference_m)
        if record_layers is not None:
            record_layers.append((w.date, list(profile.w)))
        out.append(
            DayResult(
                date=w.date,
                cover=cover,
                stage=stage,
                zr_m=zr,
                taw_mm=taw,
                threshold_mm=thr,
                storage_mm=storage,
                opening_mm=opening,
                profile_mm=profile.total(),
                rain_mm=precip,
                source_rain_mm=w.rain_mm,
                runoff_mm=q,
                irrigation_mm=irr,
                et0_mm=w.et0_mm,
                kc=kc,
                etc_mm=etc,
                eta_mm=eta,
                drainage_mm=dp,
                ks=ks,
                stress=thr is not None and storage < thr and stage not in NO_STRESS_STAGES,
                snow_mm=profile.snow_mm,
                melt_mm=melt,
                frozen=frozen,
                residue_cover=residue,
                tmax=w.tmax,
                tmin=w.tmin,
                source=w.source,
                gap=w.gap,
            )
        )
    return out


def first_stress(days: Sequence[DayResult]) -> date | None:
    return next((d.date for d in days if d.stress), None)


def longest_episode(days: Sequence[DayResult]) -> int:
    best = cur = 0
    for d in days:
        cur = cur + 1 if d.stress else 0
        best = max(best, cur)
    return best
