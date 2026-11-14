"""Planning outbox and fenced worker. PostgreSQL survives broker/process failure."""

import json
import os
import signal
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from time import monotonic

from sqlalchemy import or_, select

from field_shift.contracts.planning_v1 import FarmPlanComparison
from field_shift.modules.planning_runtime.models import PlanningDelivery
from field_shift.modules.planning_runtime.service import service
from field_shift.modules.rotations.scenario_planning.common import canonical, reason
from field_shift.modules.rotations.scenario_planning.models import PlanningRunRow


def dispatch(factory, publish, *, timestamp=None):
    timestamp = timestamp or datetime.now(UTC)
    with factory.begin() as session:
        rows = list(
            session.scalars(
                select(PlanningDelivery)
                .join(PlanningRunRow)
                .where(
                    PlanningDelivery.next_at <= timestamp,
                    or_(
                        PlanningRunRow.status == "queued",
                        (PlanningRunRow.status == "running")
                        & (PlanningRunRow.lease_until <= timestamp),
                    ),
                )
                .order_by(PlanningDelivery.next_at)
                .limit(32)
                .with_for_update(of=PlanningDelivery, skip_locked=True)
            )
        )
        ids = [r.run_id for r in rows]
        for row in rows:
            row.next_at = timestamp + timedelta(seconds=30)
    # Commit reservations before publication. Failed publication is retried after 30s.
    for identifier in ids:
        try:
            publish(identifier)
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "Planning publish failed; durable delivery will retry"
            )
            break
    return len(ids)


def publish(identifier):
    from field_shift.jobs.celery_app import planning_job

    planning_job.apply_async(
        args=(identifier,), queue="planning", routing_key="planning", retry=False
    )


def child_limits():
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (300, 300))
    resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024**2, 64 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def execute_child(factory, run, *, deadline=300):
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            [sys.executable, "-m", "field_shift.jobs.planning_execute", run.run_id],
            stdout=output,
            stderr=errors,
            start_new_session=True,
            preexec_fn=child_limits,
            env=dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1"),
        )
        started = monotonic()
        try:
            while process.poll() is None:
                if monotonic() - started >= deadline:
                    raise TimeoutError("Planning execution deadline")
                with factory() as session:
                    row = session.get(PlanningRunRow, run.run_id)
                    if row is None or row.status != "running" or row.attempt != run.attempt:
                        raise InterruptedError("Planning cancelled or superseded")
                try:
                    process.wait(timeout=min(0.5, max(0.01, deadline - (monotonic() - started))))
                except subprocess.TimeoutExpired:
                    pass
            if process.returncode:
                errors.seek(0)
                # Child emits only its exception class. Never expose validation payloads.
                code = errors.read(4096).decode("utf-8", errors="replace").strip()
                if code == "TimeoutError":
                    raise TimeoutError("Planning execution budget exhausted")
                raise RuntimeError("Planning child failed; model/source may be unavailable")
            output.seek(0)
            return json.load(output)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def process_planning(factory, identifier, *, runner=execute_child):
    with factory.begin() as session:
        run = service.claim(session, identifier)
    if run is None:
        return
    comparison = artifacts = failure = None
    try:
        result = runner(factory, run)
        comparison = FarmPlanComparison.model_validate_json(canonical(result["comparison"]))
        artifacts = result["artifacts"]
        if comparison.input_sha256 != run.input_sha256:
            raise ValueError("Worker result input identity mismatch")
    except Exception as exc:
        failure = reason(
            type(exc).__name__,
            (
                "Planning execution budget exhausted; reduce scope or explicitly raise the "
                "budget within the supported limits. No substitute prediction."
                if isinstance(exc, TimeoutError)
                else "Planning could not finish; no substitute prediction. "
                "Check model readiness and input coverage."
            ),
            identifier,
            "result",
        )
    with factory.begin() as session:
        service.finish(
            session,
            run.run_id,
            run.attempt,
            comparison=comparison,
            artifacts=artifacts if failure is None else None,
            error=failure,
        )
    # Celery acknowledges only after this transaction. Crashes recover via the outbox/lease.
