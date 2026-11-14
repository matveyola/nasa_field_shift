"""«Польовий помічник» for one saved water-balance run the farmer explicitly selected.

The run travels as a path segment of the widget's base URL (/assistant/{field}/{season}/{scenario}
/runs/{run_id}); the API loads it from the database, checks it against the field context, builds
the field context from it and signs that, never anything the browser sent."""

import base64
import hashlib
import hmac
import json
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from test_agent_evidence import NOW, FrozenDatetime, forecast, vegetation
from test_agent_field_context import historical_run
from test_remote_agent import API_KEY, SIGNING_KEY, _decode, _Stream

from field_shift.api import create_app
from field_shift.config import Settings
from field_shift.db import build_session_factory
from field_shift.jobs import repository
from field_shift.jobs.models import Job, JobKind, JobStatus
from field_shift.models import metadata
from field_shift.modules.agent import service
from field_shift.modules.farm.models import Farm
from field_shift.modules.fields.models import Field
from field_shift.modules.simulation.schemas import WaterSource
from field_shift.modules.weather.scenarios import Scenario
from field_shift.seed import seed_demo

BASE = "/api/agent/assistant/F-03/2026"
RUN_CONTEXT = BASE + "/less_rain/runs/{}/field-context?locale=en"


@pytest.fixture
def app(monkeypatch):
    factory = build_session_factory("sqlite://")
    engine = factory.kw["bind"]
    metadata.create_all(engine)
    with factory() as session:
        seed_demo(session)
    monkeypatch.setattr(service, "datetime", FrozenDatetime)
    # A current saved forecast and NDVI exist: they reach only the ordinary (no run) assistant.
    monkeypatch.setattr(service, "latest_water_forecast", lambda session, field_id: forecast())
    monkeypatch.setattr(service, "vegetation_season", lambda *args, **kwargs: vegetation())
    application = create_app(
        Settings(
            agent_url="http://agent:8000", agent_api_key=API_KEY, agent_signing_key=SIGNING_KEY
        ),
        session_factory=factory,
    )
    application.state.session_factory = factory
    try:
        yield application
    finally:
        engine.dispose()


def save_run(
    app,
    *,
    field_id="F-03",
    status=JobStatus.SUCCEEDED,
    result: dict | None = None,
    kind=JobKind.SIMULATION,
    analysis: str | None = None,
    boundary_revision=0,
) -> str:
    inputs = {"season": 2026, "scenario": "less_rain", "source": "historical"}
    if analysis is not None:
        inputs["analysis"] = analysis
    with app.state.session_factory() as session:
        job = repository.submit(
            session,
            kind=kind,
            field_id=field_id,
            inputs=inputs,
            boundary_revision=boundary_revision,
            now=NOW,
        )
        job.status = status
        if status == JobStatus.RUNNING:
            job.attempt_token = str(uuid4())
            job.lease_expires_at = NOW + timedelta(minutes=5)
        job.result = historical_run().model_dump(mode="json") if result is None else result
        session.commit()
        return job.id


def run_result(**update) -> dict:
    return historical_run().model_copy(update=update).model_dump(mode="json")


def charts(context: dict) -> list[str]:
    return [chart["chart_id"] for chart in context["available_charts"]]


# ---------------------------------------------------------------- the selected run is explained


def test_selected_historical_run_becomes_the_server_built_context(app):
    run = save_run(app)
    with TestClient(app) as client:
        response = client.get(RUN_CONTEXT.format(run))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    context = response.json()
    assert context["field_id"] == "F-03" and context["revision"] == 0
    # The historical run only: never the current forecast or NDVI next to it, or as it.
    assert charts(context) == ["c_water_season"]
    facts = {fact["id"]: fact for fact in context["facts"]}
    assert "f_forecast_min_root_zone_reserve" not in facts
    assert facts["f_historical_min_root_zone_reserve"]["quantity"] == {"value": 60, "unit": "mm"}
    assert facts["f_historical_precipitation_total"]["quantity"] == {"value": 6, "unit": "mm"}
    assert facts["f_water_stress_days"]["quantity"] == {"value": 1, "unit": "d"}
    provenance = facts["f_historical_min_root_zone_reserve"]["provenance"]
    # Modelled, from archived weather, for this scenario and period: not a measurement or forecast.
    assert provenance["kind"] == "deterministic_calc"
    assert provenance["source"] == {
        "provider": "Field Shift",
        "product_id": "FAO-56",
        "version": "fao56-test",
        "url": "https://www.ecmwf.int",
    }
    assert "Saved historical simulation for scenario less_rain" in provenance["method"]
    assert "rainfall multiplier 0.7" in provenance["method"]
    days = historical_run().days
    assert provenance["temporal"] == {
        "start": days[0].date.isoformat(),
        "end": days[-1].date.isoformat(),
    }


def test_without_a_selected_run_the_assistant_keeps_the_current_forecast_and_guesses_no_run(app):
    save_run(app)  # a completed historical run exists, but nobody selected it
    with TestClient(app) as client:
        context = client.get(BASE + "/less_rain/field-context?locale=en").json()
        # A run id in a query string is not a selection: only the path segment is.
        queried = client.get(BASE + f"/less_rain/field-context?locale=en&run_id={uuid4()}").json()
    assert charts(context) == charts(queried) == ["c_water_forecast", "c_vegetation"]
    assert not any(fact["id"].startswith("f_historical") for fact in context["facts"])


# ---------------------------------------------------------------- refused, never substituted


@pytest.mark.parametrize("run_id", ["not-a-uuid", "1234", "00000000-0000-0000-0000-00000000000g"])
def test_malformed_run_ids_are_refused_by_every_run_endpoint(app, run_id):
    with TestClient(app) as client:
        for response in (
            client.get(RUN_CONTEXT.format(run_id)),
            client.get(BASE + f"/less_rain/runs/{run_id}/health/ready"),
            client.post(BASE + f"/less_rain/runs/{run_id}/v1/agent/stream", json={}),
        ):
            assert response.status_code == 422


def test_unknown_runs_and_runs_of_another_field_or_kind_are_not_found(app):
    other_field = save_run(app, field_id="F-01", result=run_result(field_id="F-01"))
    geo = save_run(app, kind=JobKind.GEO_ML)
    forecast_job = save_run(app, analysis="water_forecast")
    rotation = save_run(app, analysis="rotation_comparison")
    with TestClient(app) as client:
        for run in (str(uuid4()), other_field, geo, forecast_job, rotation):
            response = client.get(RUN_CONTEXT.format(run))
            assert response.status_code == 404, run
            assert response.json() == {"detail": "The simulation run was not found for this field"}


def test_a_field_outside_the_callers_farm_is_not_found_even_with_its_own_run(app):
    with app.state.session_factory() as session:
        session.add(
            Farm(
                id=2,
                name="Another farm",
                season=2026,
                next_field_number=1,
                irrigation_available=False,
                priority="",
            )
        )
        session.flush()
        source = session.get(Field, "F-03")
        session.add(
            Field(
                id="X-01",
                farm_id=2,
                name="Not yours",
                crop="Maize",
                area_ha=1,
                season=2026,
                geometry=source.geometry,
            )
        )
        session.commit()
    run = save_run(app, field_id="X-01", result=run_result(field_id="X-01"))
    with TestClient(app) as client:
        response = client.get(f"/api/agent/assistant/X-01/2026/less_rain/runs/{run}/field-context")
    assert response.status_code == 404
    assert response.json() == {"detail": "Field X-01 was not found"}


@pytest.mark.parametrize(
    "status", [JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYING, JobStatus.FAILED]
)
def test_unfinished_or_failed_runs_are_a_conflict(app, status):
    run = save_run(app, status=status, result={})
    with TestClient(app) as client:
        response = client.get(RUN_CONTEXT.format(run))
    assert response.status_code == 409
    assert response.json() == {"detail": "The simulation run has no completed result"}


def test_a_succeeded_run_without_a_usable_result_is_a_conflict(app):
    empty = save_run(app)
    with app.state.session_factory() as session:
        session.get(Job, empty).result = None
        session.commit()
    broken = save_run(app, result={"field_id": "F-03", "days": "not a list"})
    with TestClient(app) as client:
        for run in (empty, broken):
            response = client.get(RUN_CONTEXT.format(run))
            assert response.status_code == 409
            assert response.json() == {"detail": "The simulation run has no completed result"}


def test_a_run_for_an_earlier_field_boundary_is_a_conflict(app):
    run = save_run(app)
    with app.state.session_factory() as session:
        session.get(Field, "F-03").boundary_revision = 1
        session.commit()
    newer_boundary = save_run(app, boundary_revision=2)
    with TestClient(app) as client:
        for job in (run, newer_boundary):
            response = client.get(RUN_CONTEXT.format(job))
            assert response.status_code == 409
            assert response.json() == {
                "detail": "The field boundary changed. Run a new simulation."
            }


@pytest.mark.parametrize(
    "path",
    [
        BASE + "/baseline/runs/{}/field-context",  # another scenario
        BASE + "/dry_year/runs/{}/field-context",  # the synthetic dry year is not less_rain
        "/api/agent/assistant/F-03/2025/less_rain/runs/{}/field-context",  # another season
    ],
)
def test_a_run_for_another_season_or_scenario_is_a_conflict(app, path):
    run = save_run(app)
    with TestClient(app) as client:
        response = client.get(path.format(run))
    assert response.status_code == 409
    assert response.json() == {
        "detail": "The simulation result does not match the requested field context"
    }


def test_a_dry_year_run_is_only_ever_explained_as_dry_year(app):
    run = save_run(app, result=run_result(scenario=Scenario.DRY_YEAR))
    with TestClient(app) as client:
        refused = client.get(RUN_CONTEXT.format(run))
        context = client.get(BASE + f"/dry_year/runs/{run}/field-context").json()
    assert refused.status_code == 409
    method = context["facts"][0]["provenance"]["method"]
    assert "scenario dry_year" in method
    assert "drought" not in json.dumps(context) and "less_rain" not in json.dumps(context)


def test_demo_runs_are_refused_not_presented_as_historical(app):
    demo = save_run(app, result=run_result(demo=True))
    demo_source = save_run(app, result=run_result(source=WaterSource.DEMO))
    no_provenance = save_run(app, result=run_result(provenance=None))
    with TestClient(app) as client:
        for run in (demo, demo_source, no_provenance):
            response = client.get(RUN_CONTEXT.format(run))
            assert response.status_code == 409
            assert response.json() == {
                "detail": "The selected run is a demo, not a historical simulation"
            }


def test_a_run_whose_saved_days_fail_the_evidence_checks_is_refused(app):
    saved = historical_run()
    future = [day.model_copy(update={"date": day.date + timedelta(days=30)}) for day in saved.days]
    gap = [saved.days[0], *saved.days[2:]]
    negative = [saved.days[0].model_copy(update={"rain_mm": -1}), *saved.days[1:]]
    with TestClient(app) as client:
        for days in (future, gap, negative):
            run = save_run(app, result=run_result(days=days))
            response = client.get(RUN_CONTEXT.format(run))
            assert response.status_code == 409
            assert response.json() == {
                "detail": "The selected run cannot be used as evidence. Run a new simulation."
            }


# ---------------------------------------------------------------- the signed turn


def _widget_turn(**extra) -> dict:
    return {
        "request_id": "req_1",
        "session_id": "ses_1",
        "locale": "en",
        "message": {"text": "Explain this run"},
        # Earlier turns are context the agent reads, never evidence it may cite.
        "history": [{"role": "assistant", "text": "Minimum reserve was 5 mm."}],
        "client": {"widget_version": "w", "capabilities": {"actions": [], "screens": []}},
        **extra,
    }


def _open_stream(app, monkeypatch) -> list:
    opened = []
    monkeypatch.setattr(
        app.state.agent_provider._opener,
        "open",
        lambda request, timeout: opened.append(request) or _Stream(b""),
    )
    return opened


def _attested(request) -> dict:
    headers = {key.casefold(): value for key, value in request.header_items()}
    version, encoded, signature = headers["x-fieldshift-context-attestation"].split(".")
    expected = hmac.digest(SIGNING_KEY.encode(), f"{version}.{encoded}".encode(), "sha256")
    assert base64.urlsafe_b64encode(expected).rstrip(b"=").decode() == signature
    return json.loads(_decode(encoded))


def test_the_selected_run_turn_signs_the_server_context_not_the_browsers(app, monkeypatch):
    run = save_run(app)
    opened = _open_stream(app, monkeypatch)
    forged = {
        "field_id": "F-03",
        "revision": 0,
        "facts": [{"id": "f_historical_min_root_zone_reserve", "quantity": {"value": 999}}],
    }
    with TestClient(app) as client:
        response = client.post(
            BASE + f"/less_rain/runs/{run}/v1/agent/stream",
            json=_widget_turn(field_context=forged, run_id=str(uuid4())),
        )
    assert response.status_code == 200
    [request] = opened
    body = json.loads(request.data)
    assert set(body) == {*_widget_turn(), "field_context"}  # the browser's run_id is dropped
    context = body["field_context"]
    assert charts(context) == ["c_water_season"]
    facts = {fact["id"]: fact for fact in context["facts"]}
    assert facts["f_historical_min_root_zone_reserve"]["quantity"]["value"] == 60
    assert body["history"] == _widget_turn()["history"]  # forwarded as context only
    signed = _attested(request)
    assert signed["field_id"] == "F-03" and signed["field_context_revision"] == 0
    assert signed["body_sha256"] == hashlib.sha256(request.data).hexdigest()
    assert signed["principal"] == "field-shift-local-farm"


def test_the_ordinary_turn_ignores_a_run_or_historical_context_the_browser_claims(app, monkeypatch):
    run = save_run(app)
    opened = _open_stream(app, monkeypatch)
    claimed = {
        "field_id": "F-03",
        "revision": 0,
        "available_charts": [{"chart_id": "c_water_season"}],
    }
    with TestClient(app) as client:
        response = client.post(
            BASE + "/less_rain/v1/agent/stream",
            json=_widget_turn(field_context=claimed, run_id=run),
        )
    assert response.status_code == 200
    [request] = opened
    context = json.loads(request.data)["field_context"]
    assert charts(context) == ["c_water_forecast", "c_vegetation"]
    assert not any(fact["id"].startswith("f_historical") for fact in context["facts"])


def test_an_invalid_run_never_reaches_the_agent(app, monkeypatch):
    opened = _open_stream(app, monkeypatch)
    stale = save_run(app, status=JobStatus.FAILED, result={})
    with TestClient(app) as client:
        for run, code in ((str(uuid4()), 404), (stale, 409)):
            response = client.post(
                BASE + f"/less_rain/runs/{run}/v1/agent/stream", json=_widget_turn()
            )
            assert response.status_code == code
    assert opened == []
    assert app.state.agent_provider._inflight == set()


def test_the_run_scoped_health_check_matches_the_ordinary_one(app, monkeypatch):
    monkeypatch.setattr(
        app.state.agent_provider,
        "_request_json",
        lambda request, *, timeout, limit: json.dumps({"status": "ready", "agent": "x"}).encode(),
    )
    with TestClient(app) as client:
        plain = client.get(BASE + "/less_rain/health/ready")
        scoped = client.get(BASE + f"/less_rain/runs/{UUID(int=1)}/health/ready")
    assert plain.status_code == scoped.status_code == 200
    assert plain.json() == scoped.json()
