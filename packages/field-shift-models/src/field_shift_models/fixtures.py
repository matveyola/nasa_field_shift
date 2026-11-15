"""Explicit SYNTHETIC benchmark fixtures. Never imported by the production provider."""

import copy
import json
from datetime import date, timedelta
from importlib.resources import files

from field_shift_contracts.planning_v1.field import CandidateSequence, FieldProfileSnapshot
from field_shift_contracts.planning_v1.weather import WeatherScenarioSet

from .engine import Parameters
from .provenance import digest


def synthetic_inputs(crop="Maize", years=1, dry=False):
    example = json.loads(
        files("field_shift_contracts.planning_v1")
        .joinpath("data/planning-contracts.json")
        .read_text()
    )
    p, c, w = (copy.deepcopy(example[k]) for k in ("field_profile", "candidate", "weather"))
    land = copy.deepcopy(p["sources"][0])
    land.update(
        id="synthetic-land",
        product="controlled synthetic land assumption",
        kind="synthetic",
        spatial_method="polygon",
        coverage_fraction=1.0,
        quality="valid",
    )
    p["sources"].append(land)
    p["land_source_ids"] = [land["id"]]
    instant = "2027-04-01T00:00:00Z"
    p["as_of"] = instant
    p["soil"][0].update(bottom_cm=240.0, salinity_ds_m=0.0, quality="valid")
    p["water_state"].update(
        observed_at=instant,
        available_at=instant,
        depth_bottom_cm=240.0,
        volumetric_m3_m3=0.3,
        available_reserve_mm=360.0,
        available_capacity_mm=360.0,
    )
    p["management"]["irrigation"] = [
        dict(season=y, max_mm=0.0, system="none", source_ids=["synthetic-input"])
        for y in range(2027, 2027 + years)
    ]
    c["assignments"], c["assessments"] = [], []
    crop_ref = dict(crop_id=crop, cultivar_id="ospy-generic", profile_version="3.1.0")
    for year in range(2027, 2027 + years):
        a = copy.deepcopy(example["candidate"]["assignments"][0])
        a.update(
            season=year,
            crop=crop_ref,
            sowing_window=dict(start=f"{year}-04-01", end=f"{year}-04-01"),
            harvest_window=dict(start=f"{year}-09-30", end=f"{year}-09-30"),
        )
        s = copy.deepcopy(example["candidate"]["assessments"][0])
        s.update(season=year, crop=crop_ref, status="eligible", unchecked_rule_ids=[])
        c["assignments"].append(a)
        c["assessments"].append(s)
    start, end = date(2027, 4, 1), date(2026 + years, 9, 30)
    days = []
    for offset in range((end - start).days + 1):
        day = copy.deepcopy(example["weather"]["scenarios"][0]["traces"][0]["days"][0])
        day.update(
            date=(start + timedelta(days=offset)).isoformat(),
            tmin_c=22.0 if dry else 14.0,
            tmax_c=36.0 if dry else 26.0,
            liquid_rain_mm=0.1 if dry else 3.0,
            et0_mm=6.0 if dry else 3.5,
            quality="valid",
            soil_frozen=False,
            snow_water_equivalent_mm=0.0,
            wind_m_s=2.0,
            relative_humidity_fraction=0.6,
        )
        days.append(day)
    w.update(
        as_of=instant,
        issued_at=instant,
        cycle_at=instant,
        stale_after="2027-04-02T00:00:00Z",
        valid_window=dict(start=start.isoformat(), end=end.isoformat()),
    )
    w["scenarios"][0]["traces"][0]["days"] = days
    for source in [*p["sources"], *w["sources"]]:
        source["checksum_sha256"] = digest(
            {"kind": "synthetic", "profile": p["soil"], "weather": days}
        )
    return (
        FieldProfileSnapshot.model_validate_json(json.dumps(p)),
        CandidateSequence.model_validate_json(json.dumps(c)),
        WeatherScenarioSet.model_validate_json(json.dumps(w)),
        Parameters(
            (80.0,),
            (100.0,),
            "synthetic-controlled-hydraulics-v1",
            True,
            uniform_initial_profile=True,
        ),
    )
