from functools import lru_cache
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Research and Outreach Framework"
    app_env: str = "development"
    debug: bool = False
    database_url: str = "postgresql+psycopg://username:password@localhost:5432/database_name"
    log_level: str = "INFO"
    allowed_domains: str = ""
    cors_allowed_origins: str = ""
    crawl4ai_base_directory: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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
            if self.app_env.lower() != "development" and parsed.scheme != "https":
                raise ValueError("CORS_ALLOWED_ORIGINS must use HTTPS outside development")
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin not in origins:
                origins.append(origin)
        if self.app_env.lower() == "development" and "http://localhost:5173" not in origins:
            origins.append("http://localhost:5173")
        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
