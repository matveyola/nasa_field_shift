from functools import lru_cache

from celery import Celery
from kombu import Exchange, Queue
from sqlalchemy.orm import Session, sessionmaker

from field_shift.config import get_settings
from field_shift.db import build_session_factory
from field_shift.jobs.models import JobKind
from field_shift.jobs.settings import get_job_settings
from field_shift.jobs.worker import process_job

settings = get_job_settings()
app = Celery("field_shift", broker=get_settings().redis_url)
app.conf.update(
    task_queues=tuple(
        Queue(kind.value, Exchange(kind.value, type="direct"), routing_key=kind.value)
        for kind in JobKind
    ),
    task_default_queue=JobKind.SIMULATION.value,
    task_create_missing_queues=False,
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_acks_late=True,
    task_acks_on_failure_or_timeout=False,
    task_reject_on_worker_lost=True,
    task_time_limit=settings.task_time_limit_seconds,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=3,
    broker_transport_options={
        "visibility_timeout": settings.visibility_timeout_seconds,
        "socket_connect_timeout": 3,
        "socket_timeout": 3,
    },
    result_backend_transport_options={"visibility_timeout": settings.visibility_timeout_seconds},
    visibility_timeout=settings.visibility_timeout_seconds,
)


@lru_cache(maxsize=1)
def _worker_factory() -> sessionmaker[Session]:
    # Initialize the connection pool in the worker child, after prefork.
    return build_session_factory(get_settings().database_url)


@app.task(name="field_shift.jobs.run")
def run_job(job_id: str) -> None:
    # A normal return follows a committed database change or identifies a duplicate.
    process_job(_worker_factory(), job_id, lease_seconds=settings.lease_seconds)
