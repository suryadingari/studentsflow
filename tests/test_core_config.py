import pytest

from app.core.config import Settings


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
