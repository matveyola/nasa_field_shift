import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agent_service.api import create_app
from agent_service.config import Settings


def test_compatible_model_and_base_url_are_read_from_environment():
    settings = Settings.from_env({
        "AGENT_AGENT": "openai",
        "AGENT_OPENAI_BASE_URL": "https://gateway.example/api/v1/",
        "AGENT_OPENAI_MODEL": "gpt-luna",
        "AGENT_OPENAI_TOKEN_LIMIT_FIELD": "max_tokens",
        "AGENT_OPENAI_REASONING_EFFORT": "none",
    })
    assert settings.openai_base_url == "https://gateway.example/api/v1"
    assert settings.openai_model == "gpt-luna"
    assert settings.openai_token_limit_field == "max_tokens"
    assert settings.openai_reasoning_effort == "none"


@pytest.mark.parametrize("url", [
    "file:///credentials",
    "http://remote.example/v1",
    "https://user:password@gateway.example/v1",
    "https://gateway.example/v1?key=value",
    "https://gateway.example:65536/v1",
    "https://gateway.example/v1/chat/completions",
])
def test_invalid_or_insecure_gateway_urls_are_rejected(url):
    with pytest.raises(ValidationError, match="API base URL"):
        Settings(agent="openai", openai_base_url=url)


def test_docker_host_http_gateway_is_supported():
    settings = Settings(agent="openai", openai_base_url="http://host.docker.internal:11434/v1")
    assert settings.openai_base_url == "http://host.docker.internal:11434/v1"


def test_openai_mode_reports_missing_credentials_without_falling_back(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = create_app(Settings(agent="openai", openai_model="gpt-luna", openai_base_url="https://gateway.example/v1"))
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        result = client.get("/health/ready")
    assert result.status_code == 503
    assert result.json()["reason"] == "llm_credentials_missing"


def test_openai_key_is_not_sent_to_an_implicit_default_gateway(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-gateway-key")
    app = create_app(Settings(agent="openai", openai_model="gpt-luna"))
    with TestClient(app) as client:
        result = client.get("/health/ready")
    assert result.status_code == 503
    assert result.json()["reason"] == "llm_configuration_missing"
