"""Actual migrations must retain existing application and library diagnostics."""

import io
import json
import logging
from pathlib import Path
from time import monotonic

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from field_shift.db import build_engine
from field_shift.modules.rotations.handoff import transport


def test_actual_migration_keeps_existing_loggers_and_configures_root(tmp_path):
    unrelated = logging.getLogger("migration_regression.preexisting_library")
    assert not unrelated.disabled
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    url = f"sqlite:///{tmp_path / 'logging-migration.sqlite'}"
    config.attributes["database_url"] = url

    command.upgrade(config, "head")

    engine = build_engine(url)
    try:
        assert {"fields", "jobs", "planning_input_bundles"} <= set(
            inspect(engine).get_table_names()
        )
    finally:
        engine.dispose()
    # Apply the framework configuration, while preserving unrelated loggers that
    # existed before an in-process upgrade/recovery or the API test sequence.
    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert len(root.handlers) == 1
    console = root.handlers[0]
    assert isinstance(console, logging.StreamHandler)
    assert console.level == logging.NOTSET
    assert console.formatter is not None
    assert not unrelated.disabled
    assert not transport.LOG.disabled
    assert unrelated.isEnabledFor(logging.WARNING)
    assert transport.LOG.isEnabledFor(logging.WARNING)

    captured = io.StringIO()
    original_stream = console.setStream(captured)
    try:
        unrelated.warning("Existing library diagnostic remains enabled")
        transport.failure_event(
            "evaluate",
            "http_error",
            monotonic(),
            status=503,
            request_id="b82a1b50-7897-4ad0-9979-ce5ec9cf363f",
        )
    finally:
        console.setStream(original_stream)
    lines = captured.getvalue().splitlines()
    assert len(lines) == 2
    assert "Existing library diagnostic remains enabled" in lines[0]
    event = json.loads(lines[1][lines[1].index("{") :])
    assert set(event) == {"event", "phase", "category", "request_id", "status", "elapsed_ms"}
    assert event | {"elapsed_ms": 0} == {
        "event": "rotation_transport_failure",
        "phase": "evaluate",
        "category": "http_error",
        "request_id": "b82a1b50-7897-4ad0-9979-ce5ec9cf363f",
        "status": 503,
        "elapsed_ms": 0,
    }
    assert 0 <= event["elapsed_ms"] <= 3_600_000
