"""Ports for the external services the agent depends on, and the one failure type they raise.

The agent sees only these interfaces. Implementations: `fixture` (M0 synthetic data, in
process), `unavailable` (not connected), `http` (the proposed v0.1 contract). Swapping a
fixture for a teammate's real service is a configuration change, not a code change.
"""

import logging
import time
from dataclasses import dataclass
from typing import Literal, Protocol

from contracts.integration import (
    CapabilitiesResponse,
    ComparisonRequest,
    ComparisonResponse,
    FieldProfileRequest,
    FieldProfileResponse,
)

log = logging.getLogger("agent_service.integrations")

FailureCategory = Literal[
    "unavailable",           # not connected, refused, 5xx, network error
    "timeout",               # deadline exceeded
    "invalid_response",      # not JSON, schema mismatch, wrong request id, mislabelled data
    "unsupported_contract",  # contract_version we do not speak
    "wrong_field",           # data for another field, or geometry that does not match the host's field
    "wrong_revision",        # result computed for another FieldContext revision
    "not_found",             # unknown field or result id
    "expired",               # result past its expires_at
    "rejected",              # the service refused the request (validation, authorization, redirect)
    "too_large",             # response bigger than the configured limit
]


class IntegrationFailure(Exception):
    """A safe-to-report failure at a service boundary. `detail` never contains secrets or bodies.

    `issues` lists contract violations as (field path, expected shape) pairs, never the actual values.
    """

    def __init__(self, category: FailureCategory, detail: str, *, retryable: bool = False,
                 issues: list[tuple[str, str]] | None = None):
        super().__init__(f"{category}: {detail}")
        self.category, self.detail, self.retryable = category, detail[:200], retryable
        self.issues = issues or []


class GeoProfileProvider(Protocol):
    mode: Literal["fixture", "http", "off"]

    async def capabilities(self, request_id: str) -> CapabilitiesResponse: ...

    async def field_profile(self, request: FieldProfileRequest) -> FieldProfileResponse: ...

    async def aclose(self) -> None: ...


class SimulationProvider(Protocol):
    mode: Literal["fixture", "http", "off"]

    async def capabilities(self, request_id: str) -> CapabilitiesResponse: ...

    async def compare(self, request: ComparisonRequest) -> ComparisonResponse: ...

    async def result(self, result_id: str, request_id: str) -> ComparisonResponse: ...

    async def aclose(self) -> None: ...


class UnavailableProvider:
    """A service that is not connected. It never returns data, invented or otherwise."""

    mode = "off"

    def __init__(self, service: Literal["geo", "simulation"]):
        self.service = service

    def _fail(self):
        raise IntegrationFailure("unavailable", f"{self.service} service is not connected")

    async def capabilities(self, request_id: str) -> CapabilitiesResponse:
        self._fail()

    async def field_profile(self, request: FieldProfileRequest) -> FieldProfileResponse:
        self._fail()

    async def compare(self, request: ComparisonRequest) -> ComparisonResponse:
        self._fail()

    async def result(self, result_id: str, request_id: str) -> ComparisonResponse:
        self._fail()

    async def aclose(self) -> None:
        return None


@dataclass
class Span:
    """One integration call, logged as one line. No farmer text, no payloads, no credentials."""

    service: str
    operation: str
    request_id: str
    started: float = 0.0

    def __post_init__(self):
        self.started = time.monotonic()

    def end(self, status: str, *, contract: str | None = None, result_id: str | None = None,
            result_status: str | None = None, data_status: str | None = None, mode: str | None = None,
            failure: str | None = None) -> None:
        log.info("integration service=%s op=%s request_id=%s status=%s duration_ms=%d contract=%s mode=%s "
                 "result_id=%s result_status=%s data_status=%s failure=%s", self.service, self.operation,
                 self.request_id, status, (time.monotonic() - self.started) * 1000, contract, mode, result_id,
                 result_status, data_status, failure)
