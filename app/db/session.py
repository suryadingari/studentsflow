from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.base import Base
from app.db.persistence import validate_storage_url
import app.models  # noqa: F401 - register every mapped table

database_url = settings.effective_database_url
_configured_url = database_url.strip()
if _configured_url.startswith("postgresql://"):
    _configured_url = _configured_url.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_async_engine(_configured_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def initialize_local_schema() -> None:
    """Create the existing mapped schema only for the local SQLite demo store."""
    if not settings.initializes_local_schema:
        raise RuntimeError("Automatic schema initialization is restricted to local SQLite demo mode.")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    validate_storage_url(database_url, allow_sqlite=settings.initializes_local_schema)
    async with SessionFactory() as session:
        yield session
