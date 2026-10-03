"""Dialect-neutral schema checks; these do not claim PostgreSQL was reached."""

import pytest
from sqlalchemy import create_engine, inspect

from app.db.base import Base
from app.db.persistence import DatabaseConfigurationError, validate_database_url
import app.models  # noqa: F401 - registers model metadata


EXPECTED_TABLES = {
    "requirements", "workflows", "workflow_steps", "agent_runs", "students",
    "student_sources", "student_evidence", "deduplication_records", "matches",
    "email_drafts", "email_draft_versions", "approval_records", "outreach_records",
    "outreach_events", "replies", "opt_outs", "follow_up_plans", "tool_calls",
    "audit_logs", "kpi_metrics",
}


def test_sqlalchemy_metadata_creates_relational_schema_in_sqlite():
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        inspector = inspect(engine)
        assert EXPECTED_TABLES.issubset(set(inspector.get_table_names()))
        assert {fk["referred_table"] for fk in inspector.get_foreign_keys("student_evidence")} == {
            "students", "student_sources"}
        assert any(index["name"] == "uq_outreach_idempotency_key"
                   for index in inspector.get_unique_constraints("outreach_records"))
        assert any(index["name"] == "uq_email_draft_version"
                   for index in inspector.get_unique_constraints("email_draft_versions"))
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.mark.parametrize("value", ["", "postgresql+psycopg://username:password@localhost:5432/database_name",
                                    "sqlite:///unsupported.db", "postgresql+psycopg:///missing_host"])
def test_invalid_or_placeholder_database_urls_fail_closed(value):
    with pytest.raises(DatabaseConfigurationError):
        validate_database_url(value)


def test_valid_postgresql_url_is_validated_without_claiming_a_connection():
    value = "postgresql+psycopg://researcher:configured-secret@db.example.test:5432/research_test"
    assert validate_database_url(value) == value
