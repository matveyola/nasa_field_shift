"""HTTP adapters for the proposed v0.1 geo and simulation contracts.

Trust boundary rules:
- base URLs come only from configuration (AGENT_GEO_URL / AGENT_SIM_URL), never from the model or a request;
- redirects are never followed (a 3xx is a rejection), so a response cannot steer us to an internal host;
- bounded timeout and bounded response size; JSON only;
- POST /comparisons is not retried (not idempotent from our side); it carries an Idempotency-Key so a
  service may de-duplicate. GETs are not retried either: the caller decides, within its own deadline;
- every body is parsed into the contract models, then checked again by `normalize`.
"""

import json
from typing import Literal, TypeVar

import httpx2
from pydantic import BaseModel, SecretStr, ValidationError

from contracts.integration import (
    INTEGRATION_VERSION,
    CapabilitiesResponse,
    ComparisonRequest,
    ComparisonResponse,
    FieldProfileRequest,
    FieldProfileResponse,
    IntegrationError,
)

from .ports import IntegrationFailure, Span

M = TypeVar("M", bound=BaseModel)

_ERROR_CATEGORY = {
    "not_found": "not_found", "field_not_authorized": "rejected", "unsupported_scenario": "rejected",
    "invalid_request": "rejected", "unsupported_contract": "unsupported_contract", "expired": "expired",
    "unavailable": "unavailable", "internal": "unavailable",
}


def _data_status(result: BaseModel) -> str | None:
    """Summary of the data_status values a response carries (e.g. "fixture"), for the trace line only."""
    text = result.model_dump_json()
    found = sorted({s for s in ("live", "cached", "fixture", "unavailable") if f'"data_status":"{s}"' in text})
    return "+".join(found) or None


class HttpService:
    def __init__(self, service: Literal["geo", "simulation"], base_url: str, *, timeout_s: float, max_bytes: int,
                 token: SecretStr | None = None, transport: httpx2.AsyncBaseTransport | None = None):
        self.service, self.max_bytes, self.token = service, max_bytes, token
        self.client = httpx2.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s, follow_redirects=False,
                                         transport=transport)

    async def call(self, method: str, path: str, model: type[M], *, request_id: str, body: BaseModel | None = None,
                   idempotency_key: str | None = None, operation: str) -> M:
        span = Span(self.service, operation, request_id)
        headers = {"Accept": "application/json", "X-Request-ID": request_id, "X-FieldShift-Contract": INTEGRATION_VERSION}
        if self.token is not None:
            headers["Authorization"] = f"Bearer {self.token.get_secret_value()}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        content = body.model_dump_json(exclude_none=True) if body is not None else None
        if content is not None:
            headers["Content-Type"] = "application/json"
        try:
            try:
                async with self.client.stream(method, path, headers=headers, content=content) as response:
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw += chunk
                        if len(raw) > self.max_bytes:
                            raise IntegrationFailure("too_large", f"response larger than {self.max_bytes} bytes")
                    status, ctype = response.status_code, response.headers.get("content-type", "")
            except httpx2.TimeoutException:
                raise IntegrationFailure("timeout", f"{self.service} service timed out", retryable=True) from None
            except httpx2.TransportError as e:
                raise IntegrationFailure("unavailable", f"{self.service} service unreachable ({type(e).__name__})",
                                         retryable=True) from None
            if 300 <= status < 400:
                raise IntegrationFailure("rejected", "redirects are not followed")
            if status >= 400:
                raise self._error(status, bytes(raw))
            if not ctype.startswith("application/json"):
                raise IntegrationFailure("invalid_response", f"expected application/json, got {ctype[:40]!r}")
            result = self._parse(bytes(raw), model)
        except IntegrationFailure as f:
            span.end("error", failure=f.category)
            raise
        info = getattr(result, "service", None)
        span.end("ok", contract=INTEGRATION_VERSION, mode=getattr(info, "mode", None),
                 result_id=getattr(result, "result_id", None), result_status=getattr(result, "status", None),
                 data_status=_data_status(result))
        return result

    def _parse(self, raw: bytes, model: type[M]) -> M:
        try:
            data = json.loads(raw)
        except ValueError:
            raise IntegrationFailure("invalid_response", "body is not JSON") from None
        if isinstance(data, dict) and "contract_version" in data and data["contract_version"] != INTEGRATION_VERSION:
            raise IntegrationFailure("unsupported_contract", f"contract_version {str(data['contract_version'])[:10]!r}")
        if isinstance(data, dict) and isinstance(data.get("service"), dict) and \
                data["service"].get("contract_version") not in (None, INTEGRATION_VERSION):
            raise IntegrationFailure("unsupported_contract", "service speaks another contract version")
        try:
            return model.model_validate_json(raw)
        except ValidationError as e:
            errors = e.errors(include_url=False, include_input=False, include_context=False)
            issues = [("/".join(str(p) for p in err["loc"]) or "/", err["msg"][:160]) for err in errors[:12]]
            where = ", ".join(path for path, _ in issues[:3])
            raise IntegrationFailure("invalid_response", f"does not match the contract at {where}", issues=issues) from None

    def _error(self, status: int, raw: bytes) -> IntegrationFailure:
        try:
            err = IntegrationError.model_validate_json(raw).error
            return IntegrationFailure(_ERROR_CATEGORY[err.code], err.message_key, retryable=err.retryable)
        except ValidationError:
            category = "not_found" if status == 404 else "unavailable" if status >= 500 else "rejected"
            return IntegrationFailure(category, f"HTTP {status}", retryable=status >= 500)

    async def aclose(self) -> None:
        await self.client.aclose()


class HttpGeoProvider:
    mode = "http"

    def __init__(self, http: HttpService):
        self.http = http

    async def capabilities(self, request_id: str) -> CapabilitiesResponse:
        return await self.http.call("GET", "/v1/capabilities", CapabilitiesResponse, request_id=request_id,
                                    operation="capabilities")

    async def field_profile(self, request: FieldProfileRequest) -> FieldProfileResponse:
        return await self.http.call("POST", "/v1/field-profile", FieldProfileResponse, request_id=request.request_id,
                                    body=request, operation="field_profile")

    async def aclose(self) -> None:
        await self.http.aclose()


class HttpSimulationProvider:
    mode = "http"

    def __init__(self, http: HttpService):
        self.http = http

    async def capabilities(self, request_id: str) -> CapabilitiesResponse:
        return await self.http.call("GET", "/v1/capabilities", CapabilitiesResponse, request_id=request_id,
                                    operation="capabilities")

    async def compare(self, request: ComparisonRequest) -> ComparisonResponse:
        return await self.http.call("POST", "/v1/comparisons", ComparisonResponse, request_id=request.request_id,
                                    body=request, idempotency_key=request.idempotency_key, operation="compare")

    async def result(self, result_id: str, request_id: str) -> ComparisonResponse:
        from urllib.parse import quote

        return await self.http.call("GET", f"/v1/comparisons/{quote(result_id, safe='')}", ComparisonResponse,
                                    request_id=request_id, operation="result")

    async def aclose(self) -> None:
        await self.http.aclose()
