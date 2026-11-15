import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from time import sleep

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from field_shift.config import get_settings
from field_shift.db import build_session_factory
from field_shift.jobs import repository
from field_shift.jobs.repository import Delivery
from field_shift.jobs.settings import JobRuntimeSettings, get_job_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DispatchCycle:
    recovered: int
    reserved: int
    published: int


def run_once(
    factory: sessionmaker[Session],
    publish: Callable[[Delivery], None],
    *,
    settings: JobRuntimeSettings,
    now: datetime | None = None,
) -> DispatchCycle:
    with factory.begin() as session:
        recovered = repository.recover_expired(session, now=now)
        deliveries = repository.reserve_deliveries(
            session, retry_seconds=settings.dispatch_retry_seconds, now=now
        )

    published = 0
    for delivery in deliveries:
        try:
            publish(delivery)
        except Exception:
            logger.warning("Broker publish failed; jobs remain in PostgreSQL", exc_info=True)
            break
        published += 1
    return DispatchCycle(recovered, len(deliveries), published)


def publish_celery(delivery: Delivery) -> None:
    from field_shift.jobs.celery_app import run_job

    run_job.apply_async(
        args=(delivery.job_id,),
        queue=delivery.kind.value,
        routing_key=delivery.kind.value,
        retry=False,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_job_settings()
    factory = build_session_factory(get_settings().database_url)
    logger.info("Starting PostgreSQL job dispatcher")
    while True:
        try:
            cycle = run_once(factory, publish_celery, settings=settings)
            from field_shift.jobs.planning import dispatch, publish

            dispatch(factory, publish)
            if cycle.recovered:
                logger.info("Recovered %s expired job leases", cycle.recovered)
        except (SQLAlchemyError, ConnectionError, OSError):
            logger.exception("Dispatcher database cycle failed; retrying")
        sleep(settings.poll_seconds)


if __name__ == "__main__":
    main()
