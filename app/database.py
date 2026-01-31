"""Database connection and session management."""

import ssl

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# Create SSL context for asyncpg - Railway requires SSL but we skip verification for internal connections
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

# Create async engine using the async-compatible URL
engine = create_async_engine(
    settings.async_database_url,
    echo=settings.environment == "development",
    connect_args={"ssl": ssl_context} if "postgresql" in settings.async_database_url else {},
)

# Create session factory
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:
    """Dependency for getting database sessions."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
