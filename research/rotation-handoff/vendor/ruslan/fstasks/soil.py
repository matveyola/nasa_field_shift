"""Soil hydraulic properties and surface runoff."""

from __future__ import annotations

from dataclasses import dataclass

SAXTON_RAWLS_REF = "Saxton & Rawls (2006), SSSAJ 70:1569, eqs. 1-2"
TR55_REF = "USDA NRCS TR-55 (1986), Table 2-2b, hydrologic soil group B"


@dataclass(frozen=True, slots=True)
class SoilHydraulics:
    theta_fc: float
    theta_wp: float
    source: str

    @property
    def awc(self) -> float:
        """Available water capacity, m3/m3."""
        return self.theta_fc - self.theta_wp

    def taw_mm(self, depth_m: float) -> float:
        """FAO-56 eq. 82: TAW = 1000 (θFC − θWP) Zr."""
        return 1000.0 * self.awc * depth_m


def saxton_rawls(sand_pct: float, clay_pct: float, om_pct: float) -> SoilHydraulics:
    """θ at -33 kPa (FC) and -1500 kPa (WP) from texture and organic matter."""
    s, c, om = sand_pct / 100, clay_pct / 100, min(max(om_pct, 0.0), 8.0)
    t1500t = -0.024 * s + 0.487 * c + 0.006 * om + 0.005 * s * om - 0.013 * c * om + 0.068 * s * c + 0.031
    t1500 = t1500t + (0.14 * t1500t - 0.02)
    t33t = -0.251 * s + 0.195 * c + 0.011 * om + 0.006 * s * om - 0.027 * c * om + 0.452 * s * c + 0.299
    t33 = t33t + (1.283 * t33t**2 - 0.374 * t33t - 0.015)
    return SoilHydraulics(
        round(t33, 4),
        round(t1500, 4),
        f"{SAXTON_RAWLS_REF}; sand {sand_pct}%, clay {clay_pct}%, OM {om_pct}%",
    )


# Curve numbers for antecedent moisture condition II, hydrologic soil group B.
CURVE_NUMBER_B = {
    "bare": 86,  # fallow, bare soil
    "residue": 83,  # fallow, crop residue cover, good condition
    "row": 78,  # row crops, straight row, good condition
    "small_grain": 75,  # small grain, straight row, good condition
}


def runoff_scs(rain_mm: float, curve_number: float) -> float:
    """SCS curve-number runoff with Ia = 0.2 S (TR-55 eq. 2-3)."""
    if rain_mm <= 0:
        return 0.0
    s = 25400.0 / curve_number - 254.0
    ia = 0.2 * s
    if rain_mm <= ia:
        return 0.0
    return (rain_mm - ia) ** 2 / (rain_mm + 0.8 * s)
