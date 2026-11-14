import pytest
from pydantic import ValidationError

from agent_service.config import Settings


def test_defaults_are_safe_for_local_development():
    s = Settings.from_env({})
    assert s.environment == "development" and s.host == "127.0.0.1"
    assert s.api_key is None and s.log_message_text is False
    assert "*" not in s.cors_origins


def test_reads_typed_values_from_environment():
    s = Settings.from_env({
        "AGENT_PORT": "9000", "AGENT_TURN_TIMEOUT_S": "12.5", "AGENT_LOG_LEVEL": "DEBUG",
        "AGENT_CORS_ORIGINS": "https://a.example, https://b.example", "AGENT_API_KEY": "x" * 40,
    })
    assert (s.port, s.turn_timeout_s, s.log_level) == (9000, 12.5, "DEBUG")
    assert s.cors_origins == ["https://a.example", "https://b.example"]
    assert "x" * 40 not in repr(s)  # the key never shows up in reprs or logs


@pytest.mark.parametrize("env", [
    {"AGENT_ENVIRONMENT": "production"},                                    # no key
    {"AGENT_ENVIRONMENT": "production", "AGENT_API_KEY": "short"},          # weak key
    {"AGENT_ENVIRONMENT": "production", "AGENT_API_KEY": "x" * 40},         # localhost CORS defaults
    {"AGENT_CORS_ORIGINS": "*"},
    {"AGENT_CORS_ORIGINS": "https://a.example/"},
    {"AGENT_AGENT": "gpt"},
    {"AGENT_PORT": "0"},
])
def test_unsafe_or_invalid_configuration_fails_at_startup(env):
    with pytest.raises(ValidationError):
        Settings.from_env(env)


def test_production_starts_with_key_and_explicit_origins():
    s = Settings.from_env({"AGENT_ENVIRONMENT": "production", "AGENT_API_KEY": "x" * 40,
                           "AGENT_CORS_ORIGINS": "https://fieldshift.example",
                           "AGENT_CONTEXT_SIGNING_KEY": "s" * 40, "AGENT_REQUIRE_CONTEXT_ATTESTATION": "true"})
    assert s.environment == "production" and s.require_context_attestation
