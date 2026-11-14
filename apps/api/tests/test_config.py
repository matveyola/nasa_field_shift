import pytest
from sqlalchemy.engine import make_url

from field_shift.config import Settings


def test_postgres_credentials_are_url_encoded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_PORT", "5437")
    monkeypatch.setenv("POSTGRES_USER", "farm:worker")
    monkeypatch.setenv("POSTGRES_PASSWORD", "example@/secret:with%?")
    monkeypatch.setenv("POSTGRES_DB", "field_shift_test")

    settings = Settings()
    url = make_url(settings.database_url)
    assert url.drivername == "postgresql+psycopg"
    assert url.username == "farm:worker"
    assert url.password == "example@/secret:with%?"
    assert (url.host, url.port, url.database) == ("postgres", 5437, "field_shift_test")
    assert "secret" not in repr(settings)


def test_explicit_url_precedes_split_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///explicit.db")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_PORT", "not-a-port")
    assert Settings().database_url == "sqlite:///explicit.db"


def test_no_host_defaults_to_local_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    assert Settings().database_url == "sqlite:///field-shift.db"
