from datetime import UTC, date, datetime

import pytest
from field_shift_science.weather.historical import ReanalysisDay, ReanalysisSeries
from field_shift_science.weather.scenarios import Scenario
from pydantic import ValidationError

from field_shift.db import build_session_factory
from field_shift.jobs.models import JobKind
from field_shift.jobs.repository import Lease
from field_shift.jobs.schemas import JobSubmission
from field_shift.jobs.worker import simulation_handler
from field_shift.models import metadata
from field_shift.modules.rotations import historical, service
from field_shift.modules.rotations.schemas import PlanUpdate
from field_shift.seed import seed_demo


def submission(kind="simulation", **options):
    return JobSubmission.model_validate(
        {"kind": kind, "field_id": "F-03", "inputs": {"season": 2026, **options}}
    )


def test_demo_inputs_retain_existing_idempotency_representation():
    assert submission(source="demo").inputs.stored() == {
        "season": 2026,
        "scenario": "dry_year",
        "irrigation_mm": 0,
    }


@pytest.mark.parametrize(
    ("kind", "options"),
    [
        ("simulation", {"source": "historical", "start_date": "2025-08-01"}),
        ("geo_ml", {"source": "historical", "initial_reserve_mm": 70}),
        ("simulation", {"source": "historical", "sample_date": "2025-08-01"}),
        ("simulation", {"analysis": "rotation_comparison"}),
        ("geo_ml", {"source": "historical", "analysis": "rotation_comparison"}),
    ],
)
def test_jobs_reject_options_for_the_wrong_analysis(kind, options):
    with pytest.raises(ValidationError):
        submission(kind, **options)


def test_rotation_job_uses_saved_plans_and_one_era5_sample(monkeypatch):
    class Provider:
        calls = 0

        def load(self, latitude, longitude, start, end):
            self.calls += 1
            assert start == date(2025, 8, 1)
            assert end == date(2025, 8, 2)
            return ReanalysisSeries(
                (ReanalysisDay(start, 0, 6), ReanalysisDay(end, 8, 6)),
                latitude,
                longitude,
                "America/Chicago",
                (0.19, 0.19, 0.19),
            )

    def unexpected_simulation(*args, **kwargs):
        raise AssertionError("Historical rotation jobs must not run demo water balances.")

    provider = Provider()
    monkeypatch.setattr(historical, "OpenMeteoHistoricalWeather", lambda: provider)
    monkeypatch.setattr(service, "calculate_water_balance", unexpected_simulation)
    job = submission(
        source="historical",
        analysis="rotation_comparison",
        scenario=Scenario.LESS_RAIN,
        start_date="2025-08-01",
        end_date="2025-08-02",
    )
    lease = Lease(
        job_id="rotation-job",
        token="lease",
        kind=JobKind.SIMULATION,
        field_id=job.field_id,
        inputs=job.inputs.stored(),
        submitted_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    factory = build_session_factory("sqlite://")
    engine = factory.kw["bind"]
    metadata.create_all(engine)
    try:
        with factory() as session:
            seed_demo(session)
            service.save_plan(
                session,
                "plan-b",
                PlanUpdate(field_id=job.field_id, season=2026, crops=("Soy", "Maize", "Wheat")),
            )
        result = historical.HistoricalRotationComparison.model_validate(
            simulation_handler(factory, lease)
        )
    finally:
        engine.dispose()

    assert provider.calls == 1
    assert result.analysis == "rotation_comparison"
    assert result.evaluated_year == 2027
    assert not result.demo
    assert [item.crop for item in result.plans[1].crops] == ["Soy", "Maize", "Wheat"]
    assert all(plan.stress_days <= 2 and plan.sensitive_days is None for plan in result.plans)
    assert all(plan.provenance.initial_theta_m3_m3 == 0.19 for plan in result.plans)
    assert all(plan.provenance.rainfall_multiplier == 0.7 for plan in result.plans)
