"""Synthetic tenancy/profile fixtures; published MIRCA cells tested separately below."""

import copy
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from field_shift_contracts.planning_v1 import ProfileWriteRequest
from field_shift_science.crops.calendar import (
    containing_cell,
    maturity_year,
    month_period,
    rolling_end,
)
from field_shift_science.fields.geometry import area_hectares
from sqlalchemy import select

from field_shift.api import create_app
from field_shift.config import Settings
from field_shift.db import build_session_factory
from field_shift.models import metadata
from field_shift.modules.access.service import Principal
from field_shift.modules.farm.models import Farm
from field_shift.modules.fields.models import Field
from field_shift.modules.fields.planning_profile.service import ProfileRepository
from field_shift.modules.fields.planting_opportunities import reference
from field_shift.modules.fields.planting_opportunities.service import opportunities
from field_shift.modules.planning_runtime.inputs import InputBundle
from field_shift.modules.planning_runtime.models import PlanningBundle, PlanningHead

ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2027, 8, 20, 12, tzinfo=UTC)
PRINCIPAL = Principal(None, 1, "Calendar test", None, "viewer")


@pytest.fixture
def factory():
    factory = build_session_factory("sqlite://")
    metadata.create_all(factory.kw["bind"])
    ring = [[30.51, 50.44], [30.53, 50.44], [30.53, 50.46], [30.51, 50.46], [30.51, 50.44]]
    with factory.begin() as session:
        session.add_all(
            [
                Farm(
                    id=i,
                    name=f"Test {i}",
                    season=2026,
                    next_field_number=2,
                    irrigation_available=False,
                    priority="water",
                )
                for i in (1, 2)
            ]
        )
        session.flush()
        for field_id, farm_id in (("F-01", 1), ("FOREIGN", 2)):
            session.add(
                Field(
                    id=field_id,
                    farm_id=farm_id,
                    name="Synthetic geometry",
                    crop="Maize",
                    season=2026,
                    area_ha=area_hectares(ring),
                    geometry={"type": "Polygon", "coordinates": [ring]},
                    boundary_revision=0,
                )
            )
    yield factory
    factory.kw["bind"].dispose()


def result(factory, planning_date=date(2027, 8, 20), years=1, **options):
    with factory() as session:
        return opportunities(session, PRINCIPAL, "F-01", planning_date, years, now=NOW, **options)


def install_profile(factory, land="arable"):
    example = json.loads(
        (ROOT / "contracts/planning-v1/examples/planning-contracts.json").read_text()
    )
    profile = example["field_profile"]
    with factory() as session:
        field = session.get(Field, "F-01")
        profile.update(
            farm_id="1",
            field_id="F-01",
            geometry={**field.geometry, "crs": "EPSG:4326"},
            geometry_revision=0,
            area_ha=field.area_ha,
            land_status=land,
        )
        for source in profile["sources"]:
            source["kind"] = "reference"  # Explicit unit fixture, never installed into production.
        saved = ProfileRepository(session).write(
            "1",
            "F-01",
            ProfileWriteRequest.model_validate_json(
                json.dumps(
                    {
                        "request_id": "calendar-profile-test",
                        "expected_revision": None,
                        "profile": profile,
                    }
                )
            ),
        )
    return saved, example


def install_reviewed(factory, *, synthetic=False):
    saved, example = install_profile(factory)
    request = example["request"]
    request["farm_id"] = "1"
    request["fields"] = [saved.model_dump(mode="json")]
    request["weather"]["scenario_type"] = "synthetic" if synthetic else "historical_replay"
    for source in request["weather"]["sources"]:
        source["kind"] = "synthetic" if synthetic else "reference"
    for scenario in request["weather"]["scenarios"]:
        scenario["traces"][0]["field_snapshot_id"] = saved.snapshot_id
        for day in scenario["traces"][0]["days"]:
            day["source_date"] = "2000" + day["date"][4:]
    request["goal"] = "water_demand"
    request["economics"] = []
    request["resources"] = []
    crop = {"crop_id": "Maize", "cultivar_id": None, "profile_version": None}
    request["enabled_crops"] = [crop]
    rule = {
        "id": "test-reviewed-maize",
        "crop": crop,
        "family": "poaceae",
        "region": "test-region",
        "source": "https://example.test/reviewed-calendar-unit-fixture",
        "review_status": "reviewed",
        "valid_window": {"start": "2027-01-01", "end": "2027-12-31"},
        "purpose": "new_sowing",
        "sowing_window": {"start": "2027-05-10", "end": "2027-05-25"},
        "growth_window": {"start": "2027-05-10", "end": "2027-09-30"},
        "base_temperature_c": 10.0,
        "required_degree_days": 1000.0,
        "require_unfrozen_sowing": True,
        "min_seasonal_supply_mm": 100.0,
        "min_return_years": 1,
        "family_return_years": 1,
        "min_ph": 5.0,
        "max_ph": 8.0,
        "min_soil_depth_cm": 100.0,
        "irrigation_systems": ["none"],
        "required_capabilities": [],
    }
    bundle = InputBundle.model_validate_json(
        json.dumps(
            {
                "template": request,
                "rulebook": {"version": request["rulebook_version"], "rules": [rule]},
                "regions": {"F-01": "test-region"},
                "hydraulics": {saved.snapshot_id: {"synthetic": synthetic}},
                "footprint": None,
                "provenance": "Unit fixture; no model invoked",
                "synthetic": synthetic,
            }
        )
    )
    with factory.begin() as session:
        session.add(
            PlanningBundle(
                id="a" * 64, farm_id=1, document=bundle.model_dump(mode="json"), model_card={}
            )
        )
        session.flush()
        session.add(PlanningHead(farm_id=1, bundle_id="a" * 64))
    return saved


def test_real_pinned_calendar_cells_and_exact_species():
    expected = {
        (42, -93.5): {"Maize": (4, 10), "Soybeans": (5, 9), "Wheat1": (10, 7)},
        (50.45, 30.52): {"Maize": (5, 9), "Wheat1": (8, 7), "Wheat2": (5, 8), "Rapeseed": (9, 8)},
    }
    for location, values in expected.items():
        rows, cell, receipt = reference.reference_at(*location)
        assert (
            cell
            and receipt["archive_sha256"]
            == "815e8704acd9c801051e48ca32440d156bd1796dc9febb8db0796cf15b83c0bb"
        )
        actual = {row["cycle_id"]: (row["planting_month"], row["maturity_month"]) for row in rows}
        for cycle, months in values.items():
            assert actual[cycle] == months
        assert all(row["crop_id"] not in {"Peas", "Pulses", "Rice"} for row in rows)
    for location in [(-22.91, -43.17), (-80, 0)]:
        rows, _, _ = reference.reference_at(*location)
        assert all(row["planting_month"] is None for row in rows)


def test_native_grid_boundaries_never_jump_to_nearest_valid_cell():
    assert containing_cell([0.0, 1.0, 2.0], 0.5) == 1
    assert containing_cell([2.0, 1.0, 0.0], 1.5) == 1
    assert containing_cell([0.0, 1.0], -0.50001) is None
    assert containing_cell([0.0, 1.0], 1.5) is None
    assert containing_cell([0.0, 1.0], float("nan")) is None
    for point in [(91, 0), (0, 181)]:
        with pytest.raises(ValueError):
            reference.reference_at(*point)


def test_month_precision_leap_and_december_january_wrap():
    assert month_period(2028, 2) == (date(2028, 2, 1), date(2028, 2, 29))
    assert month_period(2027, 2)[1] == date(2027, 2, 28)
    assert maturity_year(2027, 12, 1) == 2028
    assert maturity_year(2027, 5, 9) == 2027
    assert maturity_year(2027, 5, 5) is None
    assert rolling_end(date(2028, 2, 29), 1) == date(2029, 2, 28)


def test_august_is_not_blanket_closed_and_next_reference_is_not_profit(factory):
    data = result(factory)
    assert data.profile_status == "missing" and data.lookahead_end == date(2028, 8, 20)
    wheat = [
        r for r in data.items if r.cycle_id == "Wheat1" and r.planting_period.start.year == 2027
    ]
    assert wheat[0].calendar_status == "current_reference_month"
    assert wheat[0].harvest_year == 2028 and wheat[0].date_precision == "month"
    maize = [r for r in data.items if r.crop_id == "Maize"]
    assert {r.calendar_status for r in maize} == {"passed", "upcoming"}
    upcoming = next(r for r in maize if r.calendar_status == "upcoming")
    assert upcoming.planting_period.start == date(2028, 5, 1)
    assert all(r.profit is None and r.profit_status == "unassessed" for r in data.items)
    assert all(r.eligibility in {"review_required", "unavailable"} for r in data.items)
    assert all("current_field_profile" in r.missing_data for r in data.items)
    assert next(r for r in data.items if r.crop_id == "Peas").calendar_status == "unknown"
    dates = [r.next_reference_date for r in data.items if r.next_reference_date]
    assert dates == sorted(dates)


@pytest.mark.parametrize(
    "day,expected",
    [(date(2027, 5, 1), "upcoming"), (date(2027, 5, 20), "open"), (date(2027, 8, 20), "passed")],
)
def test_reviewed_windows_take_precedence_without_inventing_future_windows(factory, day, expected):
    install_reviewed(factory)
    data = result(factory, day)
    reviewed = [r for r in data.items if r.calendar_kind == "reviewed_window"]
    assert len(reviewed) == 1 and reviewed[0].calendar_status == expected
    assert reviewed[0].harvest_year == 2027
    assert reviewed[0].maturity_period.start == reviewed[0].maturity_period.end == date(2027, 9, 30)
    assert not any(
        r.crop_id == "Maize"
        and r.harvest_year == 2027
        and r.calendar_kind == "regional_typical_month"
        for r in data.items
    )
    assert all(r.eligibility != "eligible" for r in data.items)
    if expected == "passed":
        assert reviewed[0].eligibility == "ineligible"
        assert any(
            r.crop_id == "Maize"
            and r.harvest_year == 2028
            and r.calendar_kind == "regional_typical_month"
            for r in data.items
        )


def test_stale_boundary_and_synthetic_bundle_cannot_supply_reviewed_windows(factory):
    install_reviewed(factory)
    with factory.begin() as session:
        session.get(Field, "F-01").boundary_revision += 1
    data = result(factory)
    assert data.profile_status == "stale"
    assert all(r.calendar_kind != "reviewed_window" for r in data.items)
    assert "reviewed_bundle_profile_stale" in data.limitations


def test_synthetic_bundle_does_not_supply_production_calendar(factory):
    install_reviewed(factory, synthetic=True)
    data = result(factory)
    assert all(r.calendar_kind != "reviewed_window" for r in data.items)
    assert "synthetic_bundle_not_used" in data.limitations


def test_non_arable_profile_prevents_reference_becoming_eligibility(factory):
    install_profile(factory, land="ice")
    data = result(factory)
    assert data.profile_status == "current"
    assert all(r.eligibility == "ineligible" for r in data.items)


def test_publication_after_planning_cutoff_or_server_now_is_unavailable(factory, monkeypatch):
    data = result(factory, date(2025, 8, 1))
    assert all(r.calendar_kind == "unavailable" for r in data.items)
    original = reference.load_reference()
    changed = copy.deepcopy(original[1])
    changed["available_at"] = "2029-01-01T00:00:00Z"
    monkeypatch.setattr(reference, "load_reference", lambda: (original[0], changed))
    data = result(factory, date(2030, 8, 1))
    assert data.knowledge_as_of == NOW
    assert all(r.calendar_kind == "unavailable" for r in data.items)


def test_router_auth_scope_bounds_and_no_writes(factory):
    with TestClient(create_app(Settings(access_mode="team"), session_factory=factory)) as client:
        assert client.get("/api/fields/F-01/planting-opportunities").status_code == 401
    with TestClient(create_app(Settings(), session_factory=factory)) as client:
        before = client.get("/api/fields").json()
        response = client.get(
            "/api/fields/F-01/planting-opportunities?planning_date=2027-08-20&year_count=6"
        )
        assert response.status_code == 200, response.text
        assert response.json()["lookahead_end"] == "2033-08-20"
        assert len(response.json()["items"]) <= 256
        assert client.get("/api/fields").json() == before
        assert client.get("/api/fields/FOREIGN/planting-opportunities").status_code == 404
        for query in (
            "year_count=0",
            "year_count=7",
            "planning_date=2099-01-01&year_count=6",
            "planning_date=bad",
        ):
            assert client.get("/api/fields/F-01/planting-opportunities?" + query).status_code == 422
    with factory() as session:
        assert not list(session.scalars(select(PlanningHead)))


def test_source_receipt_matches_delivered_converter():
    _, receipt = reference.load_reference()
    from field_shift.modules.fields.planting_opportunities import prepare_reference

    path = Path(prepare_reference.__file__)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == receipt["converter_sha256"]


@pytest.mark.parametrize("change", ["geometry", "future"])
def test_unusable_profile_cannot_authorize_reviewed_calendar(factory, change):
    saved = install_reviewed(factory)
    from field_shift.modules.fields.planning_profile.models import ProfileSnapshot

    with factory.begin() as session:
        row = session.get(ProfileSnapshot, saved.snapshot_id)
        document = copy.deepcopy(row.document)
        if change == "geometry":
            document["geometry"]["coordinates"][0][0][0] += 0.001
            document["geometry"]["coordinates"][0][-1][0] += 0.001
        else:
            document["as_of"] = "2029-01-01T00:00:00Z"
        row.document = document
    data = result(factory)
    assert data.profile_status == (
        "mismatched_geometry" if change == "geometry" else "not_available_as_of"
    )
    assert all(r.calendar_kind != "reviewed_window" for r in data.items)
    assert all("current_field_profile" in r.missing_data for r in data.items)


def test_offline_converter_rejects_collisions_existing_files_and_wrong_source(tmp_path):
    from field_shift.modules.fields.planting_opportunities.prepare_reference import prepare

    output = tmp_path / "artifact.gz"
    receipt = tmp_path / "receipt.json"
    archive = tmp_path / "archive.rar"
    archive.write_bytes(b"wrong archive")
    with pytest.raises(ValueError, match="distinct"):
        prepare(tmp_path, archive, output, output, "2026-01-01T00:00:00Z")
    assert not output.exists()
    output.write_bytes(b"preserved artifact")
    with pytest.raises(ValueError, match="overwrite"):
        prepare(tmp_path, archive, output, receipt, "2026-01-01T00:00:00Z")
    assert output.read_bytes() == b"preserved artifact" and not receipt.exists()
    output.unlink()
    with pytest.raises(ValueError, match="checksum"):
        prepare(tmp_path, archive, output, receipt, "2026-01-01T00:00:00Z")
    assert not output.exists() and not receipt.exists()
