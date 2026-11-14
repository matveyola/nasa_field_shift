from dataclasses import dataclass, field
from os import getenv
from urllib.parse import urlsplit

from sqlalchemy import URL


def _database_url() -> str:
    explicit_url = getenv("DATABASE_URL")
    if explicit_url:
        return explicit_url
    host = getenv("POSTGRES_HOST")
    if not host:
        return "sqlite:///field-shift.db"
    user = getenv("POSTGRES_USER", "field_shift")
    return URL.create(
        "postgresql+psycopg",
        username=user,
        password=getenv("POSTGRES_PASSWORD"),
        host=host,
        port=int(getenv("POSTGRES_PORT", "5432")),
        database=getenv("POSTGRES_DB", user),
    ).render_as_string(hide_password=False)


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str = field(
        default_factory=lambda: getenv("FIELD_SHIFT_ENVIRONMENT", "development")
    )
    access_mode: str = field(default_factory=lambda: getenv("FIELD_SHIFT_ACCESS_MODE", "local"))
    access_secure_cookie: bool = field(
        default_factory=lambda: getenv("FIELD_SHIFT_SECURE_COOKIE", "0") == "1"
    )
    access_public_origin: str = field(
        default_factory=lambda: getenv("FIELD_SHIFT_PUBLIC_ORIGIN", "").rstrip("/")
    )
    access_bootstrap_token: str = field(
        default_factory=lambda: getenv("FIELD_SHIFT_BOOTSTRAP_TOKEN", ""), repr=False
    )
    database_url: str = field(default_factory=_database_url, repr=False)
    redis_url: str = field(
        default_factory=lambda: getenv("REDIS_URL", "redis://127.0.0.1:6379/0"), repr=False
    )
    agent_url: str = field(default_factory=lambda: getenv("FIELD_SHIFT_AGENT_URL", ""), repr=False)
    agent_api_key: str = field(
        default_factory=lambda: getenv("FIELD_SHIFT_AGENT_API_KEY", ""), repr=False
    )
    agent_signing_key: str = field(
        default_factory=lambda: getenv("FIELD_SHIFT_AGENT_SIGNING_KEY", ""), repr=False
    )

    def __post_init__(self) -> None:
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("FIELD_SHIFT_ENVIRONMENT must be development, test or production.")
        if self.access_mode not in {"local", "team"}:
            raise ValueError("FIELD_SHIFT_ACCESS_MODE must be local or team.")
        if self.access_bootstrap_token and len(self.access_bootstrap_token) < 32:
            raise ValueError("The bootstrap token must contain at least 32 characters.")
        if self.access_public_origin:
            origin = urlsplit(self.access_public_origin)
            if (
                origin.scheme not in {"http", "https"}
                or not origin.hostname
                or origin.username
                or origin.password
                or origin.path
                or origin.query
                or origin.fragment
            ):
                raise ValueError("FIELD_SHIFT_PUBLIC_ORIGIN must be an exact HTTP(S) origin.")
        if self.environment == "production" and (
            self.access_mode != "team"
            or not self.access_secure_cookie
            or not self.access_public_origin.startswith("https://")
        ):
            raise ValueError("Production requires team access, secure cookies and an HTTPS origin.")


def get_settings() -> Settings:
    return Settings()
