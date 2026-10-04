import pytest

from app.db.migrate_neon import validate_neon_database_url
from app.db.persistence import DatabaseConfigurationError


def test_neon_migration_guard_accepts_neon_postgresql_host():
    url = "postgresql+psycopg://demo:test-pass@ep-example.us-east-2.aws.neon.tech/demo_db"

    assert validate_neon_database_url(url) == url


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://demo:test-pass@localhost:5432/demo_db",
    "postgresql+psycopg://demo:test-pass@db.example.com:5432/demo_db",
])
def test_neon_migration_guard_refuses_local_or_non_neon_hosts(url):
    with pytest.raises(DatabaseConfigurationError, match="not a Neon"):
        validate_neon_database_url(url)
