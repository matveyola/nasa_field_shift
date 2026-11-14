from dataclasses import dataclass
from math import isfinite
from os import getenv


@dataclass(frozen=True, slots=True)
class JobRuntimeSettings:
    task_time_limit_seconds: int = 120
    lease_grace_seconds: int = 15
    poll_seconds: float = 2
    dispatch_retry_seconds: float = 15

    def __post_init__(self) -> None:
        for value in (
            self.task_time_limit_seconds,
            self.lease_grace_seconds,
            self.poll_seconds,
            self.dispatch_retry_seconds,
        ):
            if not isfinite(value) or value <= 0:
                raise ValueError("Job timing settings must be positive and finite.")

    @property
    def lease_seconds(self) -> int:
        return self.task_time_limit_seconds + self.lease_grace_seconds

    @property
    def visibility_timeout_seconds(self) -> int:
        return max(3600, self.lease_seconds + 60)


def get_job_settings() -> JobRuntimeSettings:
    return JobRuntimeSettings(
        task_time_limit_seconds=int(getenv("JOB_TASK_TIME_LIMIT_SECONDS", "120")),
        lease_grace_seconds=int(getenv("JOB_LEASE_GRACE_SECONDS", "15")),
        poll_seconds=float(getenv("JOB_POLL_SECONDS", "2")),
        dispatch_retry_seconds=float(getenv("JOB_DISPATCH_RETRY_SECONDS", "15")),
    )
