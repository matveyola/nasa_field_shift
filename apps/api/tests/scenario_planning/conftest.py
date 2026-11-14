"""Explicit synthetic injected DTOs; these are not observed model validation."""

import copy
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from field_shift_contracts.planning_v1 import (
    CandidateSequence,
    FarmPlanningRequest,
    SequenceEvaluation,
)
from sqlalchemy.orm import Session

from field_shift.db import build_engine
from field_shift.modules.fields.models import Field
from field_shift.seed import seed_demo

ROOT = Path(__file__).resolve().parents[4]


def parse(model, value):
    return model.model_validate_json(json.dumps(value))


@pytest.fixture
def farm_data():
    x = json.loads((ROOT / "contracts/planning-v1/examples/planning-contracts.json").read_text())
    request = copy.deepcopy(x["request"])
    request["farm_id"] = "1"
    request["fields"][0].update(farm_id="1", field_id="F-01", area_ha=10.0)
    request["weather"]["stale_after"] = "2026-12-01T00:00:00Z"
    request["goal"] = "reference_margin"
    request["reference_trajectory_id"] = "reference"
    request["enabled_crops"].append(
        {"crop_id": "Soy", "cultivar_id": None, "profile_version": "synthetic-soy-v1"}
    )
    request["resources"] = []
    for i in range(1, 3):
        field = copy.deepcopy(request["fields"][0])
        field.update(field_id=f"F-0{i + 1}", snapshot_id=f"snapshot-{i + 1}", area_ha=10.0)
        request["fields"].append(field)
        trace = copy.deepcopy(request["weather"]["scenarios"][0]["traces"][0])
        trace["field_snapshot_id"] = field["snapshot_id"]
        request["weather"]["scenarios"][0]["traces"].append(trace)
    dry = copy.deepcopy(request["weather"]["scenarios"][0])
    dry.update(trajectory_id="dry", member_id="synthetic-dry")
    request["weather"]["scenarios"].append(dry)
    candidates, evaluations = [], []
    basis = {"basis": "dry_matter", "moisture_fraction": 0.0}
    for f in request["fields"]:
        for crop in request["enabled_crops"]:
            cid = f["field_id"] + "-" + crop["crop_id"]
            c = copy.deepcopy(x["candidate"])
            c.update(sequence_id=cid, field_id=f["field_id"], field_snapshot_id=f["snapshot_id"])
            c["assignments"][0].update(field_id=f["field_id"], crop=crop)
            c["assessments"][0].update(
                field_snapshot_id=f["snapshot_id"],
                crop=crop,
                status="eligible",
                unchecked_rule_ids=[],
                checked_rule_ids=["synthetic-history-rule"],
            )
            e = copy.deepcopy(x["evaluation"])
            e.update(
                sequence_id=cid,
                evaluation_id="eval-" + cid,
                field_snapshot_id=f["snapshot_id"],
                applicability="baseline",
            )
            e["seasons"][0].update(crop=crop, status="available")
            e["seasons"][0]["irrigation_mm"].update(status="available", value=0.0, reasons=[])
            row = copy.deepcopy(e["seasons"][0])
            row["trajectory_id"] = "dry"
            e["seasons"].append(row)
            candidates.append(c)
            evaluations.append(e)
    for crop in request["enabled_crops"]:
        for scenario in request["weather"]["scenarios"]:
            wheat = crop["crop_id"] == "Wheat"
            request["economics"].append(
                dict(
                    assumption_id=crop["crop_id"] + scenario["trajectory_id"],
                    crop=crop,
                    season=2027,
                    trajectory_id=scenario["trajectory_id"],
                    price_per_t=100.0,
                    variable_cost_per_ha=100.0 if wheat else 60.0,
                    irrigation_cost_per_mm_ha=0.0,
                    currency="USD",
                    price_date="2026-11-14",
                    harvest_basis=basis,
                    source="Synthetic user-entered scenario, no empirical yield evidence",
                    available_at=request["as_of"],
                    user_yield_t_ha=(5.0 if wheat else 4.0)
                    if scenario["trajectory_id"] == "reference"
                    else (1.0 if wheat else 3.0),
                    yield_origin="user_assumption",
                )
            )
    return request, candidates, evaluations


def validated(data):
    r, c, e = data
    return (
        parse(FarmPlanningRequest, r),
        [parse(CandidateSequence, v) for v in c],
        [parse(SequenceEvaluation, v) for v in e],
    )


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.integration)])
def database(request, tmp_path):
    dbname = None
    if request.param == "postgres":
        import psycopg
        from sqlalchemy.engine import make_url

        admin_url = os.environ.get("ISSUE5_POSTGRES_ADMIN_URL")
        if not admin_url:
            pytest.skip("set ISSUE5_POSTGRES_ADMIN_URL to disposable local PostgreSQL")
        dbname = "issue5_" + uuid4().hex
        with psycopg.connect(admin_url, autocommit=True) as connection:
            from psycopg import sql

            connection.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(
                    sql.Identifier(dbname)
                )
            )
        url = (
            make_url(admin_url)
            .set(drivername="postgresql+psycopg", database=dbname)
            .render_as_string(hide_password=False)
        )
    else:
        url = f"sqlite:///{tmp_path / 'planning.sqlite3'}"
    api = Path(__file__).resolve().parents[2]
    config = Config(str(api / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0010_precision_plans")
    engine = build_engine(url)
    with Session(engine) as session:
        seed_demo(session)
        session.commit()
    baseline = None
    with Session(engine) as session:
        baseline = session.get(Field, "F-01").name
    command.upgrade(config, "0012_planning_runs")
    yield engine, config, baseline
    engine.dispose()
    if dbname:
        import psycopg
        from psycopg import sql

        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(dbname))
            )


@pytest.fixture(autouse=True)
def frozen_service_clock(monkeypatch):
    from datetime import UTC, datetime

    from field_shift.modules.rotations.scenario_planning import service

    monkeypatch.setattr(service, "now", lambda: datetime(2026, 11, 14, 12, tzinfo=UTC))
