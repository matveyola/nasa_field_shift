"""Synthetic inputs for the isolated live acceptance stack; NEVER imported by production."""

import argparse
import copy
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from field_shift_contracts.planning_v1 import ProfileWriteRequest

from field_shift.db import build_session_factory
from field_shift.modules.access.service import Principal
from field_shift.modules.fields.models import Field
from field_shift.modules.fields.planning_profile.service import ProfileRepository
from field_shift.modules.planning_runtime.inputs import InputBundle, install_bundle
from field_shift.modules.planning_runtime.service import service

ROOT = Path(__file__).resolve().parents[4]


def install_synthetic(factory):
    source = json.loads(
        (ROOT / "contracts/planning-v1/examples/planning-contracts.json").read_text()
    )
    now = datetime.now(UTC)
    year = now.year
    crops = [
        {"crop_id": c, "cultivar_id": "ospy-generic", "profile_version": "3.1.0"}
        for c in ["Maize", "Wheat"]
    ]
    profiles = []
    for index, field_id in enumerate(["F-01", "F-02"]):
        profile_time = now - timedelta(minutes=3 - index)
        with factory() as session:
            field = session.get(Field, field_id)
            p = copy.deepcopy(source["field_profile"])
            p.update(
                farm_id="1",
                field_id=field_id,
                geometry={**field.geometry, "crs": "EPSG:4326"},
                geometry_revision=field.boundary_revision,
                area_ha=field.area_ha,
                as_of=profile_time.isoformat(),
                land_status="arable",
                hazards=[],
            )
            p["soil"][0].update(bottom_cm=240.0, ph=6.5, salinity_ds_m=0.0, quality="valid")
            p["water_state"].update(
                observed_at=f"{year}-04-01T00:00:00Z",
                available_at=f"{year}-04-01T00:00:00Z",
                depth_bottom_cm=240.0,
                volumetric_m3_m3=0.3,
                available_reserve_mm=360.0,
                available_capacity_mm=360.0,
            )
            p["management"].update(
                residue_cover_fraction=0.0,
                tillage="conventional",
                equipment_ids=[],
                irrigation=[
                    {
                        "season": y,
                        "max_mm": 0.0,
                        "system": "none",
                        "source_ids": ["synthetic-input"],
                    }
                    for y in range(year, year + 3)
                ],
            )
            for s in p["sources"]:
                s.update(
                    available_at=profile_time.isoformat(),
                    acquired_at=profile_time.isoformat(),
                    retrieved_at=profile_time.isoformat(),
                    kind="synthetic",
                    quality="valid",
                    coverage_fraction=1.0,
                )
            try:
                revision = ProfileRepository(session).latest("1", field_id).profile_revision
            except Exception:
                revision = None
            payload = ProfileWriteRequest.model_validate_json(
                json.dumps(
                    {"request_id": str(uuid4()), "expected_revision": revision, "profile": p}
                )
            )
            profiles.append(ProfileRepository(session).write("1", field_id, payload))
    weather = copy.deepcopy(source["weather"])
    weather.update(
        scenario_set_id="synthetic-live-" + uuid4().hex,
        as_of=now.isoformat(),
        issued_at=now.isoformat(),
        cycle_at=now.isoformat(),
        stale_after=(now + timedelta(days=1)).isoformat(),
        scenario_type="synthetic",
        valid_window={"start": f"{year}-04-01", "end": f"{year + 2}-09-30"},
    )
    for s in weather["sources"]:
        s.update(
            available_at=now.isoformat(),
            acquired_at=now.isoformat(),
            retrieved_at=now.isoformat(),
            kind="synthetic",
            quality="valid",
            coverage_fraction=1.0,
        )
    day0 = date(year, 4, 1)
    end = date(year + 2, 9, 30)
    days = []
    for offset in range((end - day0).days + 1):
        d = copy.deepcopy(source["weather"]["scenarios"][0]["traces"][0]["days"][0])
        d.update(
            date=(day0 + timedelta(days=offset)).isoformat(),
            tmin_c=14.0,
            tmax_c=26.0,
            liquid_rain_mm=3.0,
            et0_mm=3.5,
            quality="valid",
            soil_frozen=False,
            snow_water_equivalent_mm=0.0,
            wind_m_s=2.0,
            relative_humidity_fraction=0.6,
        )
        days.append(d)
    weather["scenarios"][0]["traces"] = [
        dict(
            source["weather"]["scenarios"][0]["traces"][0],
            field_snapshot_id=p.snapshot_id,
            days=days,
        )
        for p in profiles
    ]
    request = copy.deepcopy(source["request"])
    request.update(
        request_id=str(uuid4()),
        farm_id="1",
        as_of=now.isoformat(),
        fields=[p.model_dump(mode="json") for p in profiles],
        start_season=year,
        season_count=3,
        enabled_crops=crops,
        rulebook_version="synthetic-live-v1",
        weather=weather,
        goal="reference_margin",
        reference_trajectory_id="reference",
        locks=[],
        resources=[],
        equipment=[],
        crop_areas=[],
        economics=[],
        # 16 private evaluations include process startup; this is a flow test,
        # not the separate frozen CPU benchmark or a 30-second SLA assertion.
        solver_limits={**request["solver_limits"], "max_seconds": 120},
    )
    rules = []
    calendars = []
    for crop in crops:
        for y in range(year, year + 3):
            rules.append(
                dict(
                    id=f"synthetic-{crop['crop_id']}-{y}",
                    crop=crop,
                    family="poaceae",
                    region="synthetic",
                    source="synthetic-live-calendar",
                    review_status="synthetic_test",
                    valid_window={"start": f"{y}-04-01", "end": f"{y}-09-30"},
                    purpose="new_sowing",
                    sowing_window={"start": f"{y}-04-01", "end": f"{y}-04-01"},
                    growth_window={"start": f"{y}-04-01", "end": f"{y}-09-30"},
                    base_temperature_c=5.0,
                    required_degree_days=100.0,
                    require_unfrozen_sowing=True,
                    min_seasonal_supply_mm=0.0,
                    min_return_years=1,
                    family_return_years=1,
                    min_ph=5.0,
                    max_ph=8.0,
                    min_soil_depth_cm=30.0,
                    irrigation_systems=["none"],
                    required_capabilities=["phenology"],
                )
            )
            for p in profiles:
                calendars.append(
                    dict(
                        field_id=p.field_id,
                        crop=crop,
                        season=y,
                        sowing_date=f"{y}-04-01",
                        harvest_date=f"{y}-09-30",
                        source="synthetic-live-calendar",
                        available_at=now.isoformat(),
                    )
                )
            request["economics"].append(
                dict(
                    assumption_id=f"synthetic-economics-{crop['crop_id']}-{y}",
                    crop=crop,
                    season=y,
                    trajectory_id="reference",
                    price_per_t=200.0,
                    variable_cost_per_ha=300.0,
                    irrigation_cost_per_mm_ha=0.0,
                    currency="USD",
                    price_date=now.date().isoformat(),
                    harvest_basis={"basis": "dry_matter", "moisture_fraction": 0.0},
                    source="Explicit synthetic user yield assumption; not model accuracy",
                    available_at=now.isoformat(),
                    user_yield_t_ha=3.0 if crop["crop_id"] == "Maize" else 2.5,
                    yield_origin="user_assumption",
                )
            )
    bundle = InputBundle.model_validate_json(
        json.dumps(
            dict(
                template=request,
                rulebook={"version": "synthetic-live-v1", "rules": rules},
                regions={p.field_id: "synthetic" for p in profiles},
                hydraulics={
                    p.snapshot_id: {
                        "ksat_mm_day": [80.0],
                        "penetrability_percent": [100.0],
                        "source": "synthetic-live-hydraulics",
                        "synthetic": True,
                        "uniform_initial_profile": True,
                    }
                    for p in profiles
                },
                footprint=None,
                calendars=calendars,
                provenance="Synthetic end-to-end acceptance only; no observed field outcomes",
                synthetic=True,
            )
        )
    )
    principal = Principal(None, 1, "Synthetic acceptance", None, "admin")
    with factory.begin() as session:
        identifier = install_bundle(session, bundle, principal, service)
    return identifier


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    from field_shift.config import get_settings

    parser.add_argument("--database-url", default=get_settings().database_url)
    parser.add_argument("--confirm-synthetic-test-database", action="store_true", required=True)
    args = parser.parse_args()
    print(
        "Installed explicitly synthetic test bundle",
        install_synthetic(build_session_factory(args.database_url)),
    )
