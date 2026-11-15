"""Normalize only actually emitted individual seasonal metrics; never fill gaps."""

from sqlalchemy import select

from field_shift.jobs.models import Job, JobStatus

from .schemas import SeasonalWater, SeasonalWaterRow
from .service import scoped_field


def rows_for(job):
    result = (job.result or {}).get("envelope") or {}
    payload = result.get("payload", {})
    operation = result.get("operation")
    base = {
        "job_id": job.id,
        "data_kind": result.get("data_kind", "unknown"),
        "evidence_sha256": result.get("evidence_sha256", ""),
        "input_sha256": result.get("input_sha256"),
        "engine_version": result.get("engine_version"),
    }
    rows = []
    if operation == "forecast":
        for plan in payload.get("plans", []):
            for index, member in enumerate(plan.get("members", [])):
                for season in member.get("seasons", []):
                    management = season.get("water_management") or {}
                    rows.append(
                        SeasonalWaterRow(
                            **base,
                            plan_id=plan["id"],
                            scenario_id=(
                                f"member-{index}:"
                                f"{member.get('historical_anchor_date') or 'current'}"
                            ),
                            crop_id=season["crop_id"],
                            start_date=season["planting_date"],
                            end_date=season["through_date"],
                            water_shortfall_pct=season.get("water_shortfall_pct"),
                            demand_mm=season.get("demand_mm"),
                            use_mm=season.get("modeled_use_mm"),
                            deficit_mm=season.get("deficit_mm"),
                            drought_days=season.get("drought_stress_days"),
                            crop_days=season.get("crop_days"),
                            opening_mm=season.get("opening_reserve_mm"),
                            closing_mm=season.get("ending_reserve_mm"),
                            irrigation_mm=management.get("applied_irrigation_mm"),
                            drainage_mm=management.get("modeled_drainage_mm"),
                            waterlogging_days=management.get("waterlogging_stress_days"),
                            water_stress_days=season.get("water_stress_days"),
                        )
                    )
    elif operation in {"climate_step", "climate_compare"}:
        groups = (
            [
                (candidate.get("choice_id", candidate["crop_id"]), candidate)
                for candidate in payload.get("candidates", [])
            ]
            if operation == "climate_step"
            else [
                (plan["id"], season)
                for plan in payload.get("plans", [])
                for season in plan.get("seasons", [])
            ]
        )
        for plan_id, season in groups:
            for scenario_id, scenario in season.get("scenarios", {}).items():
                metrics = scenario.get("metrics") or {}
                rows.append(
                    SeasonalWaterRow(
                        **base,
                        plan_id=plan_id,
                        scenario_id=scenario_id,
                        crop_id=scenario["crop_id"],
                        start_date=scenario["planting_date"],
                        end_date=scenario["end_date"],
                        water_shortfall_pct=metrics.get("water_shortfall_pct"),
                        demand_mm=metrics.get("demand_mm"),
                        use_mm=metrics.get("modeled_use_mm"),
                        deficit_mm=metrics.get("deficit_mm"),
                        drought_days=metrics.get("drought_stress_days"),
                        crop_days=metrics.get("crop_days"),
                        opening_mm=scenario.get("profile_opening_mm"),
                        closing_mm=scenario.get("profile_closing_mm"),
                        waterlogging_days=metrics.get("waterlogging_stress_days"),
                        water_stress_days=metrics.get("water_stress_days"),
                    )
                )
    return rows


def seasonal_water(session, principal, field_id, limit):
    field = scoped_field(session, principal, field_id)
    jobs = session.scalars(
        select(Job)
        .where(
            Job.field_id == field_id,
            Job.status == JobStatus.SUCCEEDED,
            Job.inputs["analysis"].as_string() == "rotation_handoff",
            Job.matches_boundary_revision(field.boundary_revision),
        )
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(limit)
    )
    rows = []
    for job in jobs:
        if job.analysis == "rotation_handoff" and job.inputs.get("farm_id") == principal.farm_id:
            rows.extend(rows_for(job))
    return SeasonalWater(field_id=field_id, geometry_revision=field.boundary_revision, rows=rows)
