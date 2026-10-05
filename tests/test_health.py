from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.routes import health
from app.main import app


def test_health_returns_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


class _Result:
    def scalar_one(self):
        return 1


class _Connection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _statement):
        return _Result()


class _HealthyEngine:
    def connect(self):
        return _Connection()


def test_database_health_distinguishes_live_application_from_reachable_database(monkeypatch):
    monkeypatch.setattr(health, "validate_storage_url", lambda _url, **_kwargs: "validated")
    monkeypatch.setattr(health, "engine", _HealthyEngine())

    with TestClient(app) as client:
        response = client.get("/health/database")

    assert response.status_code == 200
    assert response.json() == {"application": "alive", "database": "reachable"}


def test_database_health_returns_safe_503_and_redacted_server_diagnostic(monkeypatch, capsys):
    class _FailedEngine:
        def connect(self):
            raise OperationalError("SELECT 1", {}, Exception("password=do-not-leak"))

    monkeypatch.setattr(health, "validate_storage_url", lambda _url, **_kwargs: "validated")
    monkeypatch.setattr(health, "engine", _FailedEngine())

    with TestClient(app) as client:
        response = client.get("/health/database")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {"application": "alive", "database": "unavailable"}
    }
    logged = capsys.readouterr().err
    assert "do-not-leak" not in logged
    assert "[REDACTED]" in logged
