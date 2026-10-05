from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    app_name: str = "AI Research and Outreach Framework"
    app_env: str = "development"
    debug: bool = False
    database_url: str = "postgresql+psycopg://username:password@localhost:5432/database_name"
    log_level: str = "INFO"
    allowed_domains: str = ""
    cors_allowed_origins: str = ""
    crawl4ai_base_directory: str | None = None
    google_cse_api_key: str | None = None
    google_cse_search_engine_id: str | None = None
    email_provider: str = "mock"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_from_name: str = "Research Team"
    auth_secret_key: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def is_local_demo(self) -> bool:
        return self.app_env.strip().lower() in {"development", "local", "local-demo"}

    @property
    def database_url_is_placeholder(self) -> bool:
        value = (self.database_url or "").strip().lower()
        return not value or any(token in value for token in (
            "username:password@", "/database_name", "your_password", "changeme"))

    @property
    def effective_database_url(self) -> str:
        """Use an ignored local SQLite file for placeholder development settings only."""
        if self.database_url_is_placeholder and self.is_local_demo:
            path = Path(__file__).resolve().parents[2] / "studentsflow_local.db"
            return URL.create("sqlite+aiosqlite", database=str(path)).render_as_string(
                hide_password=False)
        return (self.database_url or "").strip()

    @property
    def initializes_local_schema(self) -> bool:
        return self.is_local_demo and self.effective_database_url.startswith("sqlite+")

    @property
    def allowed_domain_set(self) -> frozenset[str]:
        return frozenset(domain.strip() for domain in self.allowed_domains.split(",") if domain.strip())

    @property
    def cors_origin_list(self) -> list[str]:
        """Return explicit browser origins; localhost is a development-only default."""
        configured = [value.strip() for value in self.cors_allowed_origins.split(",") if value.strip()]
        origins: list[str] = []
        for value in configured:
            if "*" in value:
                raise ValueError("CORS_ALLOWED_ORIGINS must list explicit origins; wildcards are not allowed")
            parsed = urlsplit(value)
            try:
                _ = parsed.port
            except ValueError as error:
                raise ValueError("CORS_ALLOWED_ORIGINS contains an invalid port") from error
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
                raise ValueError("CORS_ALLOWED_ORIGINS entries must be HTTP(S) origins without paths or credentials")
            if not self.is_local_demo and parsed.scheme != "https":
                raise ValueError("CORS_ALLOWED_ORIGINS must use HTTPS outside development")
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin not in origins:
                origins.append(origin)
        if self.is_local_demo and "http://localhost:5173" not in origins:
            origins.append("http://localhost:5173")
        return origins

    @property
    def smtp_is_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_port and self.smtp_from_email)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
