from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.persistence import validate_database_url

_configured_url = settings.database_url.strip()
if _configured_url.startswith("postgresql://"):
    _configured_url = _configured_url.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_async_engine(_configured_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    validate_database_url(settings.database_url)
    async with SessionFactory() as session:
        yield session
