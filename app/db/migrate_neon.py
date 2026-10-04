"""Safely run and verify Alembic migrations against a Neon PostgreSQL URL."""

import asyncio
import getpass
import os
import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.logging import log_database_failure
from app.db.base import Base
from app.db.persistence import DatabaseConfigurationError, validate_database_url
import app.models  # noqa: F401 - register all mapped tables


def validate_neon_database_url(database_url: str) -> str:
    """Reject local and non-Neon targets before a production migration starts."""
    validated = validate_database_url(database_url)
    host = (make_url(validated).host or "").lower().rstrip(".")
    if not (host == "neon.tech" or host.endswith(".neon.tech")):
        raise DatabaseConfigurationError(
            "Migration refused: the configured host is not a Neon PostgreSQL host."
        )
    return validated


async def _verify_schema(database_url: str, expected_revision: str) -> tuple[str, int]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            revision = (await connection.execute(
                text("SELECT version_num FROM alembic_version")
            )).scalar_one()
            tables = set((await connection.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema()"
            ))).scalars())
            missing = set(Base.metadata.tables) - tables
            if revision != expected_revision:
                raise RuntimeError("Alembic did not reach the repository's current head revision.")
            if missing:
                raise RuntimeError("Migration verification found missing application tables.")
            return revision, len(tables)
    finally:
        await engine.dispose()


def main() -> int:
    try:
        database_url = validate_neon_database_url(
            getpass.getpass("Neon DATABASE_URL (hidden input): ")
        )
    except (DatabaseConfigurationError, EOFError, KeyboardInterrupt) as error:
        print(f"Migration refused: {error or 'no connection URL supplied'}", file=sys.stderr)
        return 2

    # Alembic reads this process-only value before considering the local .env.
    # It is neither written to disk nor included in output.
    os.environ["DATABASE_URL"] = database_url
    if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        from alembic import command
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config("alembic.ini")
        expected_revision = ScriptDirectory.from_config(config).get_current_head()
        command.upgrade(config, "head")
        revision, table_count = asyncio.run(_verify_schema(database_url, expected_revision))
    except Exception as error:
        log_database_failure(error, operation="Neon Alembic upgrade and schema verification")
        print("Neon migration or schema verification failed; see sanitized server logs.",
              file=sys.stderr)
        return 1

    print(f"Neon migration verified at {revision}; tables visible in current schema: {table_count}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
