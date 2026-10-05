import pytest

from app.core.config import Settings
from app.db.persistence import DatabaseConfigurationError, validate_database_url


def test_development_cors_keeps_local_frontend_default() -> None:
    settings = Settings(_env_file=None, app_env="development")

    assert settings.cors_origin_list == ["http://localhost:5173"]


def test_production_cors_uses_only_explicit_https_origins() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        cors_allowed_origins="https://frontend.example, https://admin.example",
    )

    assert settings.cors_origin_list == [
        "https://frontend.example",
        "https://admin.example",
    ]


@pytest.mark.parametrize("origins", ["*", "https://frontend.example/path", "http://frontend.example"])
def test_production_cors_rejects_unsafe_or_non_origin_values(origins: str) -> None:
    settings = Settings(_env_file=None, app_env="production", cors_allowed_origins=origins)

    with pytest.raises(ValueError):
        _ = settings.cors_origin_list


def test_local_development_uses_sqlite_for_placeholder_database_url() -> None:
    settings = Settings(_env_file=None, app_env="development",
                        database_url="postgresql+psycopg://username:password@localhost:5432/database_name")

    assert settings.database_url_is_placeholder
    assert settings.initializes_local_schema
    assert settings.effective_database_url.startswith("sqlite+aiosqlite:///")
    assert settings.effective_database_url.endswith("studentsflow_local.db")


def test_local_demo_mode_is_auth_optional():
    settings = Settings(_env_file=None, app_env="local-demo")

    assert settings.is_local_demo


def test_production_never_falls_back_from_placeholder_database_url():
    settings = Settings(_env_file=None, app_env="production",
                        database_url="postgresql+psycopg://username:password@localhost:5432/database_name")

    assert not settings.initializes_local_schema
    assert settings.effective_database_url.startswith("postgresql+psycopg://")
    with pytest.raises(DatabaseConfigurationError):
        validate_database_url(settings.effective_database_url)
