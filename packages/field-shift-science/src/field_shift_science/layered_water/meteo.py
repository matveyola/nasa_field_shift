"""Reference evapotranspiration (FAO-56, Allen et al. 1998).

Equation numbers refer to FAO Irrigation and Drainage Paper 56, chapter 3.
"""

from __future__ import annotations

import math

GSC = 0.0820  # solar constant, MJ m-2 min-1 (eq. 21)
SIGMA_DAY = 4.903e-9  # Stefan-Boltzmann, MJ K-4 m-2 day-1
FAO56_REF = "FAO-56 (Allen et al., 1998), ch. 3"


def sat_vp(t_c: float) -> float:
    """Saturation vapour pressure, kPa (eq. 11)."""
    return 0.6108 * math.exp(17.27 * t_c / (t_c + 237.3))


def slope_vp(t_c: float) -> float:
    """Slope of the saturation vapour pressure curve, kPa/°C (eq. 13)."""
    return 4098.0 * sat_vp(t_c) / (t_c + 237.3) ** 2


def pressure_kpa(z_m: float) -> float:
    """Atmospheric pressure from elevation (eq. 7)."""
    return 101.3 * ((293.0 - 0.0065 * z_m) / 293.0) ** 5.26


def extraterrestrial_radiation(lat_deg: float, doy: int) -> float:
    """Daily Ra, MJ m-2 day-1 (eqs. 21-25)."""
    phi = math.radians(lat_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    delta = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(delta))))
    return (
        24
        * 60
        / math.pi
        * GSC
        * dr
        * (ws * math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.sin(ws))
    )


def rs_hargreaves(tmax: float, tmin: float, lat_deg: float, doy: int, krs: float = 0.16) -> float:
    """Solar radiation from the temperature range (eq. 50); krs 0.16 for interior locations."""
    return krs * math.sqrt(max(tmax - tmin, 0.0)) * extraterrestrial_radiation(lat_deg, doy)


def eto_daily(
    tmax: float,
    tmin: float,
    rs_mj: float,
    u2: float,
    lat_deg: float,
    doy: int,
    z_m: float,
    *,
    rhmax: float | None = None,
    rhmin: float | None = None,
    rhmean: float | None = None,
    albedo: float = 0.23,
) -> float:
    """FAO-56 Penman-Monteith grass reference ET0, mm/day (eq. 6).

    Actual vapour pressure follows the FAO-56 priority: RHmax/RHmin (eq. 17),
    RHmean (eq. 19), otherwise Tmin as a dew-point proxy (eq. 48).
    """
    tmean = (tmax + tmin) / 2
    gamma = 0.665e-3 * pressure_kpa(z_m)
    es = (sat_vp(tmax) + sat_vp(tmin)) / 2
    if rhmax is not None and rhmin is not None:
        ea = (sat_vp(tmin) * rhmax / 100 + sat_vp(tmax) * rhmin / 100) / 2
    elif rhmean is not None:
        ea = rhmean / 100 * es
    else:
        ea = sat_vp(tmin)
    ea = min(ea, es)
    delta = slope_vp(tmean)
    ra = extraterrestrial_radiation(lat_deg, doy)
    rso = (0.75 + 2e-5 * z_m) * ra
    ratio = min(max(rs_mj / rso, 0.25), 1.0) if rso > 0 else 0.25
    rnl = (
        SIGMA_DAY
        * ((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4)
        / 2
        * (0.34 - 0.14 * math.sqrt(ea))
        * (1.35 * ratio - 0.35)
    )
    rn = (1 - albedo) * rs_mj - rnl
    num = 0.408 * delta * rn + gamma * 900 / (tmean + 273) * u2 * (es - ea)
    return max(0.0, num / (delta + gamma * (1 + 0.34 * u2)))


def kc_climate_adjust(kc_table: float, u2: float, rhmin: float, height_m: float) -> float:
    """Kc_mid / Kc_end adjustment for climate (eqs. 62 and 65); applied only when Kc >= 0.45."""
    if kc_table < 0.45:
        return kc_table
    u2 = min(max(u2, 1.0), 6.0)
    rhmin = min(max(rhmin, 20.0), 80.0)
    return kc_table + (0.04 * (u2 - 2) - 0.004 * (rhmin - 45)) * (height_m / 3) ** 0.3
