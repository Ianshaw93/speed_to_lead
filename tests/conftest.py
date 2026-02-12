"""Pytest fixtures for Speed to Lead tests."""

import os

# Set test environment variables BEFORE importing any app modules
os.environ.setdefault("HEYREACH_API_KEY", "test_heyreach_key")
os.environ.setdefault("DEEPSEEK_API_KEY", "test_deepseek_key")
os.environ.setdefault("DEEPSEEK_MODEL", "deepseek-chat")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test-token")
os.environ.setdefault("SLACK_CHANNEL_ID", "C123456789")
os.environ.setdefault("SLACK_SIGNING_SECRET", "test_signing_secret")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("APIFY_API_KEY", "test_apify_key")
os.environ.setdefault("SLACK_ENGAGEMENT_CHANNEL_ID", "C_TEST_ENGAGEMENT")
os.environ.setdefault("SECRET_KEY", "test_secret_key_for_testing")
os.environ.setdefault("ENVIRONMENT", "testing")

from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
def test_settings():
    """Create test settings instance."""
    from app.config import Settings

    return Settings()


@pytest_asyncio.fixture
async def test_db_engine():
    """Create a test database engine using SQLite in-memory."""
    from app.models import Base

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def test_db_session(test_db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session."""
    async_session = async_sessionmaker(
        test_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session() as session:
        yield session


@pytest_asyncio.fixture
async def test_client(test_db_engine) -> AsyncGenerator[AsyncClient, None]:
    """Create a test client for the FastAPI app with test database.

    This fixture overrides the get_db dependency to use the test database,
    ensuring API endpoints use the same database as test fixtures.
    """
    from app.main import app
    from app.database import get_db
    from app.models import Base

    # Create tables in test database
    async with test_db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Create session factory for test database
    test_session_factory = async_sessionmaker(
        test_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # Override the get_db dependency
    async def override_get_db():
        async with test_session_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    # Clean up
    app.dependency_overrides.clear()


# ============================================================
# Live API Test Support
# ============================================================


def pytest_addoption(parser):
    """Add --live option for running tests against real APIs."""
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Run live tests against real APIs (DeepSeek, etc.)",
    )


def pytest_configure(config):
    """Register the 'live' marker."""
    config.addinivalue_line(
        "markers",
        "live: mark test as requiring live API access (skip unless --live is passed)",
    )


def pytest_collection_modifyitems(config, items):
    """Skip live tests unless --live flag is passed."""
    if config.getoption("--live"):
        # --live given: don't skip live tests
        return

    skip_live = pytest.mark.skip(reason="Need --live option to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
