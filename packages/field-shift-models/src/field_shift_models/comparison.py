"""CPU decision benchmark of real engines, with explicit synthetic forcing.

PCSE demo parameter assets are benchmark-only with unverified separate permissions.
They are never selected for commercial serving by this package.
"""

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from .provenance import digest


def wofost_synthetic(crop="Maize", dry=False):
    import pcse
    from pcse.base import ParameterProvider, WeatherDataContainer, WeatherDataProvider
    from pcse.models import Wofost72_WLP_CWB
    from pcse.tests.db_input import fetch_cropdata, fetch_sitedata, fetch_soildata
    from pcse.tests.run_wofost import namedtuple_factory

    db = Path(pcse.settings.PCSE_USER_HOME) / "pcse.db"
    # Read the shipped demo; never change its tables or run its DB-writing benchmark helper.
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        conn.row_factory = namedtuple_factory
        cropdata = fetch_cropdata(conn, 31031, 2000, {"Maize": 2, "Barley": 3, "Wheat": 1}[crop])
        site = fetch_sitedata(conn, 31031, 2000)
        soil = fetch_soildata(conn, 31031)
    params = ParameterProvider(cropdata=cropdata, sitedata=site, soildata=soil)
    start, end = date(2000, 4, 1), date(2000, 9, 30)
    agro = [
        {
            start: {
                "CropCalendar": {
                    "crop_name": crop,
                    "variety_name": "bundled-demo",
                    "crop_start_date": start,
                    "crop_start_type": "sowing",
                    "crop_end_date": end,
                    "crop_end_type": "harvest",
                    "max_duration": 184,
                },
                "TimedEvents": None,
                "StateEvents": None,
            }
        }
    ]
    weather = WeatherDataProvider()
    weather.latitude, weather.longitude, weather.elevation = 50.4, 30.5, 100.0
    for stamp in pd.date_range(start, end):
        d = stamp.date()
        weather._store_WeatherDataContainer(
            WeatherDataContainer(
                LAT=50.4,
                LON=30.5,
                ELEV=100.0,
                DAY=d,
                IRRAD=18.0e6,
                TMIN=22.0 if dry else 14.0,
                TMAX=36.0 if dry else 26.0,
                VAP=12.0,
                RAIN=0.01 if dry else 0.3,
                E0=0.6 if dry else 0.35,
                ES0=0.6 if dry else 0.35,
                ET0=0.6 if dry else 0.35,
                WIND=2.0,
            ),
            d,
        )
    model = Wofost72_WLP_CWB(params, weather, agro)
    model.run_till_terminate()
    output = model.get_output()
    return {
        "yield_dry_t_ha": float(output[-1]["TWSO"] / 1000),
        "input_sha256": digest(
            {
                "crop": cropdata,
                "site": site,
                "soil": soil,
                "forcing": "synthetic-hot-dry-v1" if dry else "synthetic-temperate-v1",
                "window": [start.isoformat(), end.isoformat()],
            }
        ),
        "parameter_sha256": digest({"crop": cropdata, "site": site, "soil": soil}),
        "output_sha256": digest([{**r, "day": r["day"].isoformat()} for r in output]),
        "parameter_permission": "unverified; benchmark only",
        "scope": "generic WOFOST72 water-limited classic; different bundled soil/crop parameters "
        "and year from OSPy, no empirical accuracy or paired accuracy ranking",
    }


def reference_bucket(dry=False):
    from types import SimpleNamespace

    from field_shift.modules.crops.catalog import demo_crop_profile
    from field_shift.modules.simulation.water_balance import FAO56_BUCKET_VERSION, fao56_days

    # Named existing reference, using its declared demo crop coefficients (not invented yield).
    profile = demo_crop_profile("Maize")
    forcing = [
        SimpleNamespace(
            date=d.date(), rain_mm=0.1 if dry else 3.0, reference_et0_mm=6.0 if dry else 3.5
        )
        for d in pd.date_range("2027-04-01", "2027-09-30")
    ]
    days = fao56_days(
        forcing,
        initial_mm=360.0,
        capacity_mm=360.0,
        threshold_mm=180.0,
        coefficient=profile.water_use_factor,
    )
    return {
        "model_version": FAO56_BUCKET_VERSION,
        "coefficient": profile.water_use_factor,
        "coefficient_scope": "existing demo Maize profile, not locally calibrated",
        "closing_reserve_mm": days[-1].reserve_mm,
        "yield_t_ha": None,
        "output_sha256": digest([d.model_dump(mode="json") for d in days]),
        "max_balance_error_mm": max(
            abs(
                d.reserve_mm
                - d.opening_mm
                - d.rain_mm
                - d.irrigation_mm
                + d.water_use_mm
                + d.drainage_mm
            )
            for d in days
        ),
    }
