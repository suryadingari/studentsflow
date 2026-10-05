from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.logging import log_database_failure
from app.db.persistence import DatabaseConfigurationError, validate_storage_url
from app.db.session import database_url, engine

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/database")
async def database_health_check() -> dict[str, str]:
    """Report database readiness separately from application liveness."""
    try:
        validate_storage_url(database_url, allow_sqlite=settings.initializes_local_schema)
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except (DatabaseConfigurationError, SQLAlchemyError) as error:
        log_database_failure(error, operation="GET /health/database")
        raise HTTPException(
            status_code=503,
            detail={"application": "alive", "database": "unavailable"},
        ) from error
    return {"application": "alive", "database": "reachable"}
