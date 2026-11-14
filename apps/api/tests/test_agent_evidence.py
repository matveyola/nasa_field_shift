from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from field_shift.db import build_session_factory
from field_shift.errors import ConflictError
from field_shift.jobs import repository
from field_shift.jobs.models import JobKind, JobStatus
from field_shift.models import metadata
from field_shift.modules.agent import service
from field_shift.modules.agent.provider import AgentContext, DemoAgentProvider
from field_shift.modules.agent.schemas import AgentAction, AgentRequest
from field_shift.modules.fields.models import Field, FieldPlanting
from field_shift.modules.fields.service import require_field
from field_shift.modules.imagery.vegetation import vegetation_footprint
from field_shift.modules.imagery.vegetation_schemas import (
    Heterogeneity,
    VegetationMonthResult,
    VegetationObservation,
    VegetationSeason,
)
from field_shift.modules.simulation.schemas import WaterForecast, WaterForecastDay
from field_shift.seed import seed_demo

TODAY = date(2026, 11, 10)
NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is not None else NOW.replace(tzinfo=None)


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    factory = build_session_factory("sqlite://")
    engine = factory.kw["bind"]
    metadata.create_all(engine)
    with factory() as session:
        seed_demo(session)
    monkeypatch.setattr(service, "datetime", FrozenDatetime)
    try:
        yield factory
    finally:
        engine.dispose()


def forecast(field_id="F-03", values=(80.0, 70.0, 60.0)) -> WaterForecast:
    return WaterForecast(
        field_id=field_id,
        season=2026,
        crop="Maize",
        initialized_at=NOW - timedelta(days=1),
        generated_at=NOW,
        method="fao56",
        physical_model_version="fao56-test",
        provider="ECMWF IFS HRES",
        source_url="https://www.ecmwf.int",
        grid_resolution_km=9,
        grid_latitude=41.7,
        grid_longitude=-93.5,
        root_depth_m=1,
        initial_reserve_mm=90,
        capacity_mm=150,
        threshold_mm=65,
        field_capacity=0.3,
        wilting_point=0.15,
        crop_coefficient=1,
        limitations=[],
        days=[
            WaterForecastDay(
                date=TODAY + timedelta(days=index),
                reserve_mm=value,
                opening_mm=90,
                rain_mm=0,
                irrigation_mm=0,
                water_use_mm=0,
                drainage_mm=0,
                lead_days=index + 1,
                root_zone_estimate_mm=1,
            )
            for index, value in enumerate(values)
        ],
    )


def vegetation(*, day=TODAY - timedelta(days=3), low_area=18.0) -> VegetationSeason:
    observation = VegetationObservation(
        date=day,
        acquired_at=datetime.combine(day, datetime.min.time(), UTC),
        scene_id="S2_TEST",
        mean_ndvi=0.61,
        p10_ndvi=0.38,
        p90_ndvi=0.81,
        valid_percent=88,
        pixel_count=120,
        catalog_url="https://earth-search.aws.element84.com",
    )
    return VegetationSeason(
        field_id="F-03",
        season=day.year,
        boundary_revision=0,
        geometry_fingerprint="test",
        generated_at=NOW,
        status="partial",
        observations=[observation],
        heterogeneity=Heterogeneity(
            date=day,
            median_ndvi=0.62,
            spread_ndvi=0.43,
            low_area_percent=low_area,
            valid_percent=88,
            low_threshold=0.47,
            high_threshold=0.77,
            resolution_m=20,
            cells=[],
        ),
    )


def context(**kwargs) -> AgentContext:
    return AgentContext(None, None, (), field_id="F-03", season=2026, today=TODAY, **kwargs)


def save_forecast(session: Session, *, field_id="F-03", revision=0):
    job = repository.submit(
        session,
        kind=JobKind.SIMULATION,
        field_id=field_id,
        boundary_revision=revision,
        inputs={
            "season": 2026,
            "scenario": "baseline",
            "source": "forecast",
            "analysis": "water_forecast",
        },
        now=NOW,
    )
    job.status = JobStatus.SUCCEEDED
    job.result = forecast(field_id).model_dump(mode="json")
    session.commit()
    return job.id


def save_vegetation(session: Session):
    field = require_field(session, "F-03")
    _, fingerprint = vegetation_footprint(session, field, 2026)
    data = vegetation()
    snapshot = VegetationMonthResult(
        field_id=field.id,
        season=2026,
        month=11,
        boundary_revision=0,
        geometry_fingerprint=fingerprint,
        generated_at=NOW,
        cutoff=TODAY,
        status="ready",
        observation=data.observations[0],
        heterogeneity=data.heterogeneity,
    )
    job = repository.submit(
        session,
        kind=JobKind.GEO_ML,
        field_id=field.id,
        inputs={
            "analysis": "vegetation",
            "season": 2026,
            "source": "historical",
            "sample_date": TODAY.isoformat(),
            "vegetation_mask_hash": fingerprint,
        },
        now=NOW,
    )
    job.status = JobStatus.SUCCEEDED
    job.result = snapshot.model_dump(mode="json")
    session.commit()


def test_forecast_reply_uses_actual_dates_threshold_and_physical_values():
    answer = DemoAgentProvider().reply("Explain water risk", context(forecast=forecast()))
    assert answer.demo is True
    assert "2026-11-12" in answer.message
    assert "minimum root-zone reserve 60 mm" in answer.message
    assert "threshold 65 mm" in answer.message
    assert "2026-11-10–2026-11-12" in answer.message
    assert "ECMWF IFS HRES" in answer.message
    assert "2026-11-09 12:00 UTC" in answer.message
    assert answer.actions[0].forecast_day == date(2026, 11, 12)
    assert answer.actions[0].run_id is None


@pytest.mark.parametrize(
    "update",
    [
        {"field_id": "F-02"},
        {"season": 2025},
        {"initialized_at": NOW - timedelta(days=4)},
        {"initialized_at": NOW + timedelta(days=1)},
        {"threshold_mm": float("nan")},
        {"days": []},
        {"days": forecast().days[1:]},
        {"days": [forecast().days[0], forecast().days[2]]},
        {"days": [forecast().days[0], forecast().days[0]]},
    ],
)
def test_invalid_stale_and_other_field_forecasts_never_declare_water_safety(update):
    answer = DemoAgentProvider().reply(
        "water", context(forecast=forecast().model_copy(update=update))
    )
    assert "No current verified water forecast" in answer.message
    assert answer.actions[0].forecast_day is None


def test_ukrainian_query_gets_real_field_evidence_in_ukrainian():
    answer = DemoAgentProvider().reply(
        "Які поля потребують уваги?",
        context(
            forecast=forecast(),
            vegetation=vegetation(),
        ),
    )
    assert "поріг 65,0 мм" in answer.message
    assert "середній NDVI 0,61" in answer.message
    assert "88,0%" in answer.message
    assert len(answer.actions) == 2
    assert answer.actions[1].analysis == "vegetation"


def test_vegetation_cue_has_explicit_observed_area_date_coverage_and_non_diagnostic_rule():
    answer = DemoAgentProvider().reply("NDVI", context(vegetation=vegetation()))
    assert "2026-11-07" in answer.message
    assert "mean NDVI 0.61" in answer.message
    assert "88%" in answer.message
    assert "18% of observed area" in answer.message
    assert "at least 0.15 below" in answer.message
    assert "inspection heuristic" in answer.message
    assert "10%" in answer.message
    assert answer.actions[0].target == "fields"


def test_vegetation_rule_is_not_a_health_claim_and_stale_image_does_not_trigger_inspection():
    clear = DemoAgentProvider().reply("NDVI", context(vegetation=vegetation(low_area=5)))
    assert "does not confirm the absence" in clear.message
    assert "Inspect the highlighted" not in clear.message
    old = DemoAgentProvider().reply(
        "NDVI",
        context(
            vegetation=vegetation(
                day=TODAY - timedelta(days=31),
                low_area=90,
            )
        ),
    )
    assert "over 30 days old" in old.message
    assert "Inspect the highlighted" not in old.message


def test_insufficient_cloud_coverage_or_wrong_field_never_becomes_a_vegetation_signal():
    for changes in ({"field_id": "F-02"}, {"season": 2025}, {"observations": []}):
        result = vegetation().model_copy(update=changes)
        answer = DemoAgentProvider().reply("NDVI", context(vegetation=result))
        assert "No verified Sentinel-2 observation" in answer.message
    for changes in ({"valid_percent": 69.9}, {"pixel_count": 9}, {"mean_ndvi": float("nan")}):
        result = vegetation()
        result.observations[0] = result.observations[0].model_copy(update=changes)
        answer = DemoAgentProvider().reply("NDVI", context(vegetation=result))
        assert "No verified Sentinel-2 observation" in answer.message


def test_chat_loads_only_persisted_matching_evidence_and_releases_connection(database):
    with database() as session:
        save_forecast(session)
        save_vegetation(session)
        payload = AgentRequest(field_id="F-03", season=2026, scenario="baseline", message="Inspect")

        class InspectProvider:
            def reply(self, message, evidence):
                assert not session.in_transaction()
                assert evidence.water is None
                assert evidence.forecast.field_id == "F-03"
                assert evidence.vegetation.observations[0].mean_ndvi == 0.61
                assert evidence.today == TODAY
                return DemoAgentProvider().reply(message, evidence)

        answer = service.chat(session, payload, InspectProvider())
        assert "60 mm" in answer.message and "0.61" in answer.message
        history = service.conversation_detail(session, answer.conversation_id)
        assert history.messages[-1].actions[1].analysis == "vegetation"


def test_past_season_and_old_boundary_cannot_use_current_forecast(database):
    with database() as session:
        save_forecast(session)
        past = AgentRequest(field_id="F-03", season=2025, scenario="baseline", message="Water")
        assert (
            "No current verified water forecast"
            in service.chat(
                session,
                past,
                DemoAgentProvider(),
            ).message
        )
        field = session.get(Field, "F-03")
        field.boundary_revision = 1
        session.commit()
        current = past.model_copy(update={"season": 2026})
        assert (
            "No current verified water forecast"
            in service.chat(
                session,
                current,
                DemoAgentProvider(),
            ).message
        )


def test_changed_planting_while_provider_runs_prevents_saving_stale_evidence(database):
    with database() as session:
        save_vegetation(session)
        payload = AgentRequest(field_id="F-03", season=2026, scenario="baseline", message="NDVI")

        class ChangePlantingProvider:
            def reply(self, message, evidence):
                with database() as other:
                    other.add(
                        FieldPlanting(
                            field_id="F-03",
                            season=2026,
                            area_ha=1,
                            geometry={
                                "type": "Polygon",
                                "coordinates": [
                                    [
                                        [-93.5, 41.7],
                                        [-93.499, 41.7],
                                        [-93.499, 41.701],
                                        [-93.5, 41.7],
                                    ]
                                ],
                            },
                        )
                    )
                    other.commit()
                return DemoAgentProvider().reply(message, evidence)

        with pytest.raises(ConflictError, match="planted area changed"):
            service.chat(session, payload, ChangePlantingProvider())


@pytest.mark.parametrize(
    "options",
    [
        {"target": "rotations", "analysis": "vegetation"},
        {"target": "fields", "forecast_day": TODAY},
        {"target": "water", "forecast_day": TODAY, "run_id": UUID(int=1)},
        {"target": "fields", "run_id": UUID(int=1)},
    ],
)
def test_action_contract_rejects_mixed_or_wrong_route_context(options):
    with pytest.raises(ValidationError):
        AgentAction(label="Open", **options)
