"""Typed service configuration, read from `AGENT_*` environment variables."""

import os
from collections.abc import Mapping
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from .paths import knowledge_index_path

ENV_PREFIX = "AGENT_"
LOCAL_DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    environment: Literal["development", "test", "production"] = "development"
    host: str = "127.0.0.1"
    port: int = Field(8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: list[str] = Field(default_factory=lambda: list(LOCAL_DEV_ORIGINS))
    # deterministic / deterministic-dev: M1 rule-based agent. scripted-llm: offline fake model through
    # the real LLM loop (free demo, CI). gemini: the primary real model (google-genai SDK; GEMINI_API_KEY).
    # anthropic: Claude, optional and never a fallback. Credentials always come from the SDK's own lookup.
    agent: Literal["deterministic", "deterministic-dev", "scripted-llm", "gemini", "anthropic", "openai"] = "deterministic"
    api_key: SecretStr | None = None                    # Bearer token for /v1/*; required in production
    turn_timeout_s: float = Field(120.0, gt=0, le=600)  # wall-clock budget for one whole agent turn
    max_body_bytes: int = Field(262_144, ge=1024)       # request body limit (JSON)
    log_message_text: bool = False                      # never log farmer text unless explicitly enabled

    # Gemini (AGENT_AGENT=gemini). Thinking: low (default, economical), medium, high (opt-in). No "minimal".
    gemini_model: str = Field("gemini-3.8-flash", pattern=r"^[a-z0-9][a-z0-9.\-]{2,63}$")
    gemini_thinking_level: Literal["low", "medium", "high"] = "low"
    # An explicit base URL keeps a gateway credential tied to its configured endpoint.
    openai_base_url: str | None = None
    openai_model: str | None = Field(default=None, max_length=200)
    openai_token_limit_field: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"
    openai_reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None
    # Optional cost estimate in logs: published USD prices per million tokens (time-sensitive; unset = no estimate).
    llm_price_input_usd_per_mtok: float | None = Field(None, ge=0)
    llm_price_output_usd_per_mtok: float | None = Field(None, ge=0)   # thinking tokens are billed as output
    llm_price_cached_input_usd_per_mtok: float | None = Field(None, ge=0)  # input read from the cache; unset = input price

    # LLM loop limits (all LLM modes). llm_model / llm_effort / llm_refusal_fallback / llm_strict_tools: anthropic only.
    llm_model: str = Field("claude-opus-5-5", pattern=r"^[a-z0-9][a-z0-9.\-]{2,63}$")
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    llm_max_output_tokens: int = Field(4096, ge=256, le=32_000)   # per model call
    llm_max_rounds: int = Field(6, ge=1, le=12)                   # model calls per turn
    llm_max_tool_calls: int = Field(12, ge=1, le=32)              # tool executions per turn
    llm_max_tool_calls_per_round: int = Field(6, ge=1, le=8)
    llm_max_context_chars: int = Field(60_000, ge=8_000, le=400_000)
    llm_max_retries: int = Field(2, ge=0, le=5)                   # SDK retries for 408/409/429/5xx/connection
    llm_request_timeout_s: float = Field(60.0, gt=0, le=600)      # per model call
    llm_tool_timeout_s: float = Field(5.0, gt=0, le=60)
    llm_max_repairs: int = Field(1, ge=0, le=2)                   # grounding rewrites before redaction
    llm_refusal_fallback: bool = True                             # server-side fallbacks="default" on refusals
    llm_strict_tools: bool = True                                 # strict tool schemas (args still re-validated)

    # Knowledge base (search_knowledge tool; LLM modes only). Built offline: see README › Knowledge base.
    # lexical needs no model; dense/hybrid need the bge-m3 weights; hybrid_rerank also needs the reranker.
    rag_mode: Literal["off", "lexical", "dense", "hybrid", "hybrid_rerank"] = "lexical"
    rag_index_path: str = Field(default_factory=lambda: str(knowledge_index_path()))
    rag_timeout_s: float = Field(20.0, gt=0, le=120)             # per search_knowledge call
    rag_required: bool = False                                    # if true, /health/ready fails without an index
    rag_model_cache: str | None = None                            # Hugging Face cache folder with the pinned models
    rag_reranker_dtype: Literal["float32", "bfloat16"] = "float32"  # embedder dtype comes from the index metadata
    rag_rerank_top: int = Field(20, ge=1, le=50)                  # fused candidates re-scored by the cross-encoder

    # Weather forecast for the field (agent_service/weather.py). off (default, no network in tests) | open-meteo
    # (free for non-commercial use, CC BY 4.0, no key).
    weather_provider: Literal["off", "open-meteo"] = "off"
    weather_url: str = "https://api.open-meteo.com/v1/forecast"
    weather_timeout_s: float = Field(8.0, gt=0, le=60)

    # External services (M5, proposed contract v0.1). off: not connected (default). fixture: M0 synthetic
    # data in process (development/tests only). http: the configured service URL.
    geo_provider: Literal["off", "fixture", "http"] = "off"
    geo_url: str | None = None
    sim_provider: Literal["off", "fixture", "http"] = "off"
    sim_url: str | None = None
    integration_timeout_s: float = Field(8.0, gt=0, le=60)      # geo calls and capability checks
    sim_timeout_s: float = Field(30.0, gt=0, le=120)            # one compare_rotations call
    integration_max_bytes: int = Field(2_000_000, ge=10_000, le=20_000_000)
    integration_token: SecretStr | None = None                  # server-to-server bearer for geo/sim; never sent to browsers
    # Per-request context attestation (M6B, agent_service/attestation.py). The host backend signs each request
    # (principal, field, revision, body hash, expiry) with this key; only then are its field facts host-attested and
    # its principal a verified identity. Never shared with the browser. Separate from AGENT_API_KEY on purpose.
    context_signing_key: SecretStr | None = None
    # true: refuse any agent request without a valid attestation (the field-authorization gate). Forced in production.
    require_context_attestation: bool = False

    @field_validator("openai_base_url")
    @classmethod
    def _validate_openai_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().rstrip("/")
        try:
            url = urlsplit(value)
            host = url.hostname
            port = url.port
            valid = (
                host is not None
                and (port is None or port > 0)
                and url.username is None
                and url.password is None
                and "?" not in value
                and "#" not in value
                and "\\" not in value
                and not any(c.isspace() for c in value)
                and not url.path.rstrip("/").endswith("/chat/completions")
                and (
                    url.scheme == "https"
                    or url.scheme == "http" and host in {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
                )
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Use an HTTPS API base URL or a local HTTP endpoint, without credentials or query parameters")
        return value

    @field_validator("openai_model")
    @classmethod
    def _validate_openai_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        model = value.strip()
        if not model or any(char.isspace() for char in model):
            raise ValueError("Use the exact model ID from the configured provider")
        return model

    @field_validator("cors_origins")
    @classmethod
    def _explicit_origins(cls, origins: list[str]) -> list[str]:
        for origin in origins:
            if origin == "*" or not origin.startswith(("http://", "https://")) or origin.endswith("/"):
                raise ValueError(f"CORS origin must be an explicit scheme://host[:port], got {origin!r}")
        return origins

    @model_validator(mode="after")
    def _attestation_is_consistent(self):
        key = self.context_signing_key.get_secret_value() if self.context_signing_key else None
        if key is not None and len(key) < 32:
            raise ValueError("AGENT_CONTEXT_SIGNING_KEY must have at least 32 characters")
        if key is not None and self.api_key is not None and key == self.api_key.get_secret_value():
            raise ValueError("AGENT_CONTEXT_SIGNING_KEY must differ from AGENT_API_KEY")
        if self.require_context_attestation and key is None:
            raise ValueError("AGENT_REQUIRE_CONTEXT_ATTESTATION=true needs AGENT_CONTEXT_SIGNING_KEY")
        if self.environment == "production" and not self.require_context_attestation:
            raise ValueError("production requires AGENT_REQUIRE_CONTEXT_ATTESTATION=true (and AGENT_CONTEXT_SIGNING_KEY): "
                             "every request must carry the host backend's signed field authorization")
        return self

    @model_validator(mode="after")
    def _integrations_are_consistent(self):
        for name in ("geo", "sim"):
            mode, url = getattr(self, f"{name}_provider"), getattr(self, f"{name}_url")
            if mode == "http":
                if not url or not url.startswith(("http://", "https://")):
                    raise ValueError(f"AGENT_{name.upper()}_PROVIDER=http needs AGENT_{name.upper()}_URL (http:// or https://)")
                if self.environment == "production" and not url.startswith("https://"):
                    raise ValueError(f"production requires https for AGENT_{name.upper()}_URL")
            if mode == "fixture" and self.environment == "production":
                raise ValueError(f"AGENT_{name.upper()}_PROVIDER=fixture is synthetic test data: not allowed in production")
        return self

    @model_validator(mode="after")
    def _production_is_locked(self):
        if self.environment == "production":
            if self.api_key is None or len(self.api_key.get_secret_value()) < 32:
                raise ValueError("production requires AGENT_API_KEY with at least 32 characters")
            if any("localhost" in o or "127.0.0.1" in o for o in self.cors_origins):
                raise ValueError("production must not allow localhost CORS origins")
        return self

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        environ = os.environ if environ is None else environ
        if environ.get(ENV_PREFIX + "CONTEXT_ATTESTED"):
            # Removed in M6B: a global flag trusted every holder of the shared API key. Fail loudly, never silently.
            raise ValueError("AGENT_CONTEXT_ATTESTED was removed (it trusted any caller holding the API key). Use per-request "
                             "attestation: AGENT_CONTEXT_SIGNING_KEY and the X-FieldShift-Context-Attestation header.")
        raw: dict[str, object] = {}
        for name in cls.model_fields:
            value = environ.get(ENV_PREFIX + name.upper())
            if value is None or value == "":
                continue
            raw[name] = [o.strip() for o in value.split(",") if o.strip()] if name == "cors_origins" else value
        raw.setdefault("rag_index_path", str(knowledge_index_path(environ)))
        return cls.model_validate(raw)
