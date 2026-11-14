"""Signed, bounded transport for the private Field Shift agent runtime."""

import base64
import hashlib
import hmac
import json
import re
import secrets
from collections import deque
from collections.abc import Iterator
from datetime import UTC, datetime
from http.client import HTTPException as HttpTransportError
from threading import Lock
from time import monotonic, time
from typing import Annotated, Literal, cast
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

from pydantic import BaseModel, Field, StringConstraints, ValidationError

from field_shift.modules.agent.field_context import build_field_context
from field_shift.modules.agent.provider import AgentAnswer, AgentContext
from field_shift.modules.agent.schemas import (
    AgentAction,
    AgentCitation,
    AgentProviderName,
    AgentStatus,
)

MAX_REQUEST_BYTES = 256 * 1024
MAX_REPLY_BYTES = 1024 * 1024
TURN_TIMEOUT = 50
LIVE_PROVIDERS = frozenset({"gemini", "anthropic", "openai"})
PROVIDERS = LIVE_PROVIDERS | {"deterministic", "scripted-llm"}
SOURCE_HOSTS = frozenset(
    {
        "fao.org",
        "www.fao.org",
        "earth-search.aws.element84.com",
        "open-meteo.com",
        "www.ecmwf.int",
        "cds.climate.copernicus.eu",
    }
)


class RemoteAgentError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


# The widget's AgentRequest keys the API forwards.
# The field context is always rebuilt on the server.
ASSISTANT_KEYS = ("request_id", "session_id", "locale", "message", "history", "client")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _attestation(key: bytes, *, field_id: str, revision: int, body: bytes) -> str:
    issued = int(time())
    payload = {
        "v": 1,
        "principal": "field-shift-local-farm",
        "field_id": field_id,
        "field_context_revision": revision,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "iat": issued,
        "exp": issued + 120,
        "jti": secrets.token_urlsafe(12),
    }
    encoded = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = hmac.digest(key, f"v1.{encoded}".encode(), "sha256")
    return f"v1.{encoded}.{_b64(signature)}"


ShortText = Annotated[str, StringConstraints(min_length=1, max_length=500)]


class _Text(BaseModel):
    type: Literal["text"]
    markdown: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]


class _Question(BaseModel):
    type: Literal["clarifying_question"]
    text: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    options: Annotated[list[ShortText], Field(max_length=6)] = Field(default_factory=list)


class _Citation(BaseModel):
    id: ShortText
    title: ShortText
    url: Annotated[str, StringConstraints(max_length=2048)]
    publisher: ShortText
    year: int


class _FactRef(BaseModel):
    id: ShortText


class _Action(BaseModel):
    field_context_revision: int
    label: ShortText
    action: dict


class _Meta(BaseModel):
    provider: ShortText


class _Warning(BaseModel):
    code: ShortText


class _Reply(BaseModel):
    blocks: Annotated[
        list[Annotated[_Text | _Question, Field(discriminator="type")]],
        Field(min_length=1, max_length=16),
    ]
    facts: Annotated[list[_FactRef], Field(max_length=256)] = Field(default_factory=list)
    citations: Annotated[list[_Citation], Field(max_length=24)] = Field(default_factory=list)
    ui_actions: Annotated[list[_Action], Field(max_length=24)] = Field(default_factory=list)
    warnings: Annotated[list[_Warning], Field(max_length=24)] = Field(default_factory=list)
    meta: _Meta


def _source_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname in SOURCE_HOSTS
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
        )
    except ValueError:
        return False


def _citations(reply: _Reply, field_context: dict) -> tuple[AgentCitation, ...]:
    citations = {
        item.id: AgentCitation.model_validate(item.model_dump())
        for item in reply.citations
        if _source_url(item.url)
    }
    seen_urls = {citation.url for citation in citations.values()}
    used_fact_ids = {fact.id for fact in reply.facts}
    for fact in field_context["facts"]:
        source = fact["provenance"].get("source") or {}
        url = source.get("url")
        if (
            fact["id"] in used_fact_ids
            and isinstance(url, str)
            and url not in seen_urls
            and _source_url(url)
        ):
            citations.setdefault(
                fact["id"],
                AgentCitation(
                    id=fact["id"],
                    title=source["product_id"],
                    publisher=source["provider"],
                    url=url,
                ),
            )
            seen_urls.add(url)
    return tuple(citations.values())[:12]


def _safe_actions(
    reply: _Reply, field_context: dict, context: AgentContext
) -> tuple[AgentAction, ...]:
    revision = field_context["revision"]
    layers = {layer["layer_id"] for layer in field_context["available_layers"]}
    charts = {chart["chart_id"]: chart["metric_ids"] for chart in field_context["available_charts"]}
    ranges = {series["metric_id"]: series["range"] for series in field_context["timeseries_index"]}
    actions: list[AgentAction] = []
    for candidate in reply.ui_actions:
        if candidate.field_context_revision != revision:
            continue
        action = candidate.action
        common = {"label": candidate.label, "field_revision": revision}
        match action.get("type"):
            case "map.set_layers" if (
                action.get("layer_ids") == ["l_ndvi_variability"] and "l_ndvi_variability" in layers
            ):
                actions.append(
                    AgentAction(
                        **common, target="fields", analysis="vegetation", layer="ndvi_variability"
                    )
                )
            case "map.focus_field":
                actions.append(AgentAction(**common, target="fields"))
            case "chart.focus":
                chart_id = action.get("chart_id")
                if not isinstance(chart_id, str) or chart_id not in charts:
                    continue
                metrics = action.get("metric_ids", [])
                if not isinstance(metrics, list) or any(m not in charts[chart_id] for m in metrics):
                    continue
                if chart_id == "c_vegetation":
                    actions.append(AgentAction(**common, target="fields", analysis="vegetation"))
                elif chart_id == "c_water_season" and context.run_id is not None:
                    actions.append(AgentAction(**common, target="water", run_id=context.run_id))
                elif chart_id == "c_water_forecast" and context.forecast is not None:
                    highlight = action.get("highlight")
                    start = highlight.get("start") if isinstance(highlight, dict) else None
                    bounds = ranges[charts[chart_id][0]]
                    day = next(
                        (
                            d.date
                            for d in context.forecast.days
                            if d.date.isoformat() == start
                            and bounds["start"] <= start <= bounds["end"]
                        ),
                        None,
                    )
                    actions.append(AgentAction(**common, target="water", forecast_day=day))
        # Any provider-requested changes to persisted constraints, scenarios or rotations
        # stay outside this read-only chat. Even "auto" map actions require a user click.
    return tuple(actions[:6])


def _provider(value: object) -> AgentProviderName | None:
    if value in ("deterministic-dev", "deterministic"):
        return "deterministic"
    return cast(AgentProviderName, value) if isinstance(value, str) and value in PROVIDERS else None


class RemoteAgentProvider:
    def __init__(self, url: str, api_key: str, signing_key: str):
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._signing_key = signing_key.encode()
        self._configured = self._valid_settings(url, api_key, signing_key)
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())
        self._lock = Lock()
        self._started: deque[float] = deque()
        self._inflight: set[str] = set()

    @staticmethod
    def _valid_settings(url: str, api_key: str, signing_key: str) -> bool:
        try:
            parsed = urlsplit(url)
            return (
                bool(parsed.hostname)
                and (
                    parsed.scheme == "https"
                    or (
                        parsed.scheme == "http"
                        and parsed.hostname in {"agent", "localhost", "127.0.0.1", "::1"}
                    )
                )
                and parsed.username is None
                and parsed.password is None
                and not (parsed.query or parsed.fragment)
                and bool(re.fullmatch(r"[!-~]{32,512}", api_key))
                and len(signing_key) >= 32
                and api_key != signing_key
            )
        except ValueError:
            return False

    def _request_json(self, request: Request, *, timeout: int, limit: int) -> bytes:
        try:
            with self._opener.open(request, timeout=timeout) as response:
                if response.headers.get_content_type() != "application/json":
                    raise RemoteAgentError(502, "The assistant returned an invalid reply.")
                data = response.read(limit + 1)
        except HTTPError as error:
            error.close()
            if error.code == 429:
                raise RemoteAgentError(429, "The assistant is busy. Try again shortly.") from None
            if error.code in (408, 504):
                raise RemoteAgentError(504, "The assistant took too long. Try again.") from None
            raise RemoteAgentError(503, "The assistant is temporarily unavailable.") from None
        except TimeoutError:
            raise RemoteAgentError(504, "The assistant took too long. Try again.") from None
        except (OSError, HttpTransportError, ValueError):
            raise RemoteAgentError(503, "The assistant is temporarily unavailable.") from None
        if len(data) > limit:
            raise RemoteAgentError(502, "The assistant returned an invalid reply.")
        return data

    def assistant_health(self) -> tuple[int, dict]:
        """The agent's readiness for the widget: mode, model, knowledge and rotations only."""
        if not self._configured:
            return 503, {"status": "not_configured"}
        try:
            raw = json.loads(
                self._request_json(Request(f"{self._url}/health/ready"), timeout=3, limit=64 * 1024)
            )
        except (RemoteAgentError, ValueError):
            return 503, {"status": "unavailable"}
        if not isinstance(raw, dict) or raw.get("status") != "ready":
            return 503, {"status": "not_ready"}
        knowledge = raw.get("knowledge") if isinstance(raw.get("knowledge"), dict) else {}
        integrations = raw.get("integrations") if isinstance(raw.get("integrations"), dict) else {}
        sim = integrations.get("simulation")
        sim = sim if isinstance(sim, dict) else {}
        return 200, {
            "status": "ready",
            "agent": raw.get("agent") if isinstance(raw.get("agent"), str) else None,
            "model": raw.get("model") if isinstance(raw.get("model"), str) else None,
            "knowledge": {"mode": knowledge.get("mode")},
            "integrations": {
                "simulation": {
                    key: sim.get(key) for key in ("connected", "compare_rotations", "service_mode")
                }
            },
        }

    def stream(self, payload: dict, field_context: dict) -> Iterator[bytes]:
        """One widget turn: the server-built field context replaces the browser's, the body is
        signed, and the agent's SSE frames pass back unchanged (the widget checks every frame)."""
        if not self._configured:
            raise RemoteAgentError(503, "The assistant is not configured.")
        body = {key: payload[key] for key in ASSISTANT_KEYS if key in payload}
        body["field_context"] = field_context
        session_id = body.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise RemoteAgentError(422, "The request has no session.")
        encoded = json.dumps(
            body, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
        if len(encoded) > MAX_REQUEST_BYTES:
            raise RemoteAgentError(
                413, "The selected field context is too large for the assistant."
            )
        request = Request(
            f"{self._url}/v1/agent/stream",
            data=encoded,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "X-FieldShift-Context-Attestation": _attestation(
                    self._signing_key,
                    field_id=field_context["field_id"],
                    revision=field_context["revision"],
                    body=encoded,
                ),
            },
        )
        self._start(session_id)

        def release() -> None:
            with self._lock:
                self._inflight.discard(session_id)

        try:
            # The timeout bounds each read; the agent sends keep-alives while it works.
            response = self._opener.open(request, timeout=TURN_TIMEOUT)
        except HTTPError as error:
            release()
            error.close()
            if error.code == 429:
                raise RemoteAgentError(429, "The assistant is busy. Try again shortly.") from None
            if 400 <= error.code < 500:
                raise RemoteAgentError(400, "The assistant could not use this request.") from None
            raise RemoteAgentError(503, "The assistant is temporarily unavailable.") from None
        except (TimeoutError, OSError, HttpTransportError, ValueError):
            release()
            raise RemoteAgentError(503, "The assistant is temporarily unavailable.") from None
        if response.headers.get_content_type() != "text/event-stream":
            response.close()
            release()
            raise RemoteAgentError(502, "The assistant returned an invalid reply.")

        def frames() -> Iterator[bytes]:
            total = 0
            try:
                while chunk := response.read1(16 * 1024):
                    total += len(chunk)
                    if total > MAX_REPLY_BYTES:
                        return  # the widget reports a stream that ended without an answer
                    yield chunk
            except (TimeoutError, OSError, HttpTransportError, ValueError):
                return
            finally:
                response.close()
                release()

        return frames()

    def status(self) -> AgentStatus:
        provider = None
        if self._configured:
            try:
                raw = json.loads(
                    self._request_json(
                        Request(f"{self._url}/health/ready"), timeout=2, limit=32 * 1024
                    )
                )
                if isinstance(raw, dict) and raw.get("status") == "ready":
                    name = raw.get("agent")
                    if isinstance(name, str):
                        provider = _provider(name.removeprefix("llm/"))
            except (RemoteAgentError, ValueError):
                pass
        return AgentStatus(
            provider=provider, demo=provider not in LIVE_PROVIDERS, available=provider is not None
        )

    def _start(self, conversation_key: str) -> None:
        now = monotonic()
        with self._lock:
            while self._started and self._started[0] <= now - 60:
                self._started.popleft()
            if (
                len(self._started) >= 10
                or len(self._inflight) >= 2
                or conversation_key in self._inflight
            ):
                raise RemoteAgentError(429, "The assistant is busy. Try again shortly.")
            self._started.append(now)
            self._inflight.add(conversation_key)

    def reply(self, message: str, context: AgentContext) -> AgentAnswer:
        if not self._configured or context.field is None:
            raise RemoteAgentError(503, "The assistant is not configured.")
        request_id = uuid4().hex
        session_id = str(context.conversation_id or uuid4())
        locale = context.locale or ("uk" if re.search(r"[а-яіїєґ]", message.casefold()) else "en")
        field_context = build_field_context(
            context.field,
            season=context.season,
            locale=locale,
            now=context.captured_at or datetime.now(UTC),
            forecast=context.forecast,
            vegetation=context.vegetation,
            water=context.water,
            scenario=context.scenario,
            scouting=context.scouting,
        )
        body = json.dumps(
            {
                "request_id": request_id,
                "session_id": session_id,
                "locale": locale,
                "message": {"text": message},
                "history": [
                    {"role": turn.role, "text": turn.text[:3000]} for turn in context.history[-10:]
                ],
                "field_context": field_context,
                "client": {"widget_version": "field-shift-web"},
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode()
        if len(body) > MAX_REQUEST_BYTES:
            raise RemoteAgentError(
                413, "The selected field context is too large for the assistant."
            )
        signature = _attestation(
            self._signing_key,
            field_id=context.field.id,
            revision=field_context["revision"],
            body=body,
        )
        request = Request(
            f"{self._url}/v1/agent/message",
            data=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-FieldShift-Context-Attestation": signature,
            },
        )
        self._start(session_id)
        try:
            raw = self._request_json(request, timeout=TURN_TIMEOUT, limit=MAX_REPLY_BYTES)
        finally:
            with self._lock:
                self._inflight.remove(session_id)
        try:
            reply = _Reply.model_validate_json(raw)
        except ValidationError:
            raise RemoteAgentError(502, "The assistant returned an invalid reply.") from None
        provider = _provider(reply.meta.provider)
        if provider is None:
            raise RemoteAgentError(502, "The assistant returned an invalid reply.")
        text = "\n\n".join(
            block.markdown if isinstance(block, _Text) else block.text for block in reply.blocks
        )
        options = tuple(
            dict.fromkeys(
                option
                for block in reply.blocks
                if isinstance(block, _Question)
                for option in block.options
            )
        )[:6]
        return AgentAnswer(
            text[:20_000],
            provider not in LIVE_PROVIDERS or any(w.code == "fixture_data" for w in reply.warnings),
            _safe_actions(reply, field_context, context),
            provider,
            _citations(reply, field_context),
            options,
        )
