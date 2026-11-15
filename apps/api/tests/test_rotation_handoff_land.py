"""Real profile persistence regressions; metadata are explicit unit fixtures, not observations."""

import copy
import json
from datetime import UTC, datetime, timedelta
from importlib.resources import files

import pytest
from field_shift_contracts.planning_v1 import ProfileWriteRequest

from field_shift.db import build_session_factory
from field_shift.jobs import repository
from field_shift.jobs.models import Job, JobStatus
from field_shift.jobs.worker import InvalidModelInput
from field_shift.models import metadata
from field_shift.modules.access.service import Principal
from field_shift.modules.farm.models import Farm
from field_shift.modules.fields.models import Field
from field_shift.modules.fields.planning_profile.models import ProfileSnapshot
from field_shift.modules.fields.planning_profile.service import ProfileRepository
from field_shift.modules.rotations.handoff.land import land_status
from field_shift.modules.rotations.handoff.schemas import HandoffSubmission
from field_shift.modules.rotations.handoff.service import submit
from field_shift.modules.rotations.handoff.worker import current_field, succeed

NOW = datetime(2026, 11, 15, tzinfo=UTC)
OWNER = Principal(None, 1, "test", None, "admin")


@pytest.fixture
def workspace():
    document = json.loads(
        files("field_shift_contracts.planning_v1")
        .joinpath("data/planning-contracts.json")
        .read_text()
    )["field_profile"]
    document.update(farm_id="1", field_id="F-01", geometry_revision=2)
    # The generic source deliberately describes soil, not land. Marked unit metadata
    # exercises provenance decisions; it does not assert that these are real measurements.
    document["sources"][0].update(kind="reference", product="unit-soil-profile", quality="valid")
    source = copy.deepcopy(document["sources"][0])
    source.update(
        id="unit-land-evidence",
        product="unit-land-classification",
        spatial_method="polygon",
        coverage_fraction=1,
        quality="valid",
    )
    document["sources"].append(source)
    document["land_source_ids"] = [source["id"]]
    factory = build_session_factory("sqlite://")
    metadata.create_all(factory.kw["bind"])
    with factory.begin() as session:
        session.add(
            Farm(
                id=1,
                name="Unit profile",
                season=2026,
                next_field_number=2,
                irrigation_available=False,
                priority="water",
            )
        )
        session.flush()
        session.add(
            Field(
                id="F-01",
                farm_id=1,
                name="Unit profile geometry",
                crop="Unknown",
                season=2026,
                area_ha=document["area_ha"],
                boundary_revision=2,
                geometry={k: document["geometry"][k] for k in ("type", "coordinates")},
            )
        )
    yield factory, document
    factory.kw["bind"].dispose()


def save(factory, document, *, expected=None):
    request = ProfileWriteRequest.model_validate_json(
        json.dumps(
            {
                "request_id": f"profile-{expected}",
                "expected_revision": expected,
                "profile": document,
            }
        )
    )
    with factory() as session:
        return ProfileRepository(session).write("1", "F-01", request)


def read_land(factory, *, now=NOW):
    with factory() as session:
        return land_status(session, session.get(Field, "F-01"), now=now)


@pytest.mark.parametrize("method", ["polygon", "area_weighted"])
def test_complete_explicit_land_source_qualifies_current_profile(workspace, method):
    factory, document = workspace
    document["sources"][-1]["spatial_method"] = method
    save(factory, document)
    assert read_land(factory) == "arable"


@pytest.mark.parametrize(
    "change",
    [
        {"quality": "missing"},
        {"quality": "rejected"},
        {"quality": "partial"},
        {"coverage_fraction": 0},
        {"coverage_fraction": 0.9},
        {"coverage_fraction": None},
        {"kind": "synthetic"},
        {"spatial_method": "representative_point"},
        {"spatial_method": "none"},
    ],
)
def test_unqualified_land_source_never_authorizes_arable(workspace, change):
    factory, document = workspace
    document["sources"][-1].update(change)
    save(factory, document)
    assert read_land(factory) == "unknown"


def test_valid_weather_or_soil_source_is_not_land_proof(workspace):
    factory, document = workspace
    document["sources"] = document["sources"][:1]
    document.pop("land_source_ids")
    save(factory, document)
    assert read_land(factory) == "unknown"


def test_missing_and_zero_coverage_all_sources_reproduces_review_case(workspace):
    factory, document = workspace
    for source in document["sources"]:
        source.update(quality="missing", coverage_fraction=0)
    save(factory, document)
    assert read_land(factory) == "unknown"


@pytest.mark.parametrize("land", ["ice", "ocean", "non_arable"])
def test_complete_nonarable_evidence_remains_blocking(workspace, land):
    from fastapi import HTTPException

    factory, document = workspace
    document["land_status"] = land
    save(factory, document)
    assert read_land(factory) == land
    with factory() as session, pytest.raises(HTTPException) as error:
        submit(
            session,
            OWNER,
            "F-01",
            HandoffSubmission(request_id="blocked", geometry_revision=2, operation="forecast"),
        )
    assert error.value.status_code == 422


@pytest.mark.parametrize(
    "stale", ["revision", "geometry", "document_scope", "as_of", "availability", "retrieval"]
)
def test_stale_or_future_context_never_qualifies(workspace, stale):
    factory, document = workspace
    saved = save(factory, document)
    with factory.begin() as session:
        field = session.get(Field, "F-01")
        if stale == "revision":
            field.boundary_revision += 1
        elif stale == "geometry":
            field.geometry = {
                "type": "Polygon",
                "coordinates": [[[31, 50], [31.01, 50], [31.01, 50.01], [31, 50.01], [31, 50]]],
            }
        else:
            row = session.get(ProfileSnapshot, saved.snapshot_id)
            updated = copy.deepcopy(row.document)
            if stale == "document_scope":
                updated["farm_id"] = "2"
            else:
                future = (NOW + timedelta(days=1)).isoformat()
                if stale != "retrieval":
                    updated["as_of"] = future
                else:
                    updated["sources"][-1]["retrieved_at"] = future
                if stale == "availability":
                    for source in updated["sources"]:
                        source.update(acquired_at=future, available_at=future, retrieved_at=future)
            row.document = updated
    assert read_land(factory) == "unknown"


def test_profile_loss_between_submission_and_commit_blocks_saved_rank(workspace):
    factory, document = workspace
    save(factory, document)
    with factory.begin() as session:
        job = submit(
            session,
            OWNER,
            "F-01",
            HandoffSubmission(request_id="start", geometry_revision=2, operation="forecast"),
        )
        lease = repository.claim(session, job.job_id, lease_seconds=135)
        assert lease.inputs["land_status"] == "arable"
    document["sources"][-1].update(quality="missing", coverage_fraction=0)
    save(factory, document, expected=1)
    with factory() as session, pytest.raises(InvalidModelInput, match="arable"):
        current_field(session, lease)
    with factory.begin() as session:
        assert succeed(session, lease, {"envelope": {"ranking_usable": True}})
    with factory() as session:
        job = session.get(Job, job.job_id)
        assert job.status == JobStatus.FAILED and job.result is None


def test_missing_profile_stays_unknown(workspace):
    factory, _ = workspace
    assert read_land(factory) == "unknown"
