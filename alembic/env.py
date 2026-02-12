import os
import time
from logging.config import fileConfig

from sqlalchemy import create_engine, pool
from sqlalchemy.exc import OperationalError

from alembic import context

# Set default env vars for migrations if not set
os.environ.setdefault("HEYREACH_API_KEY", "placeholder")
os.environ.setdefault("DEEPSEEK_API_KEY", "placeholder")
os.environ.setdefault("SLACK_BOT_TOKEN", "placeholder")
os.environ.setdefault("SLACK_CHANNEL_ID", "placeholder")
os.environ.setdefault("DATABASE_URL", "postgresql://localhost/speed_to_lead")
os.environ.setdefault("SECRET_KEY", "placeholder")

from app.config import settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_sync_url() -> str:
    """Get database URL for sync driver (psycopg2)."""
    url = settings.database_url
    # Convert any async driver to sync psycopg2
    if "+asyncpg" in url:
        url = url.replace("+asyncpg", "")
    if "+aiosqlite" in url:
        url = url.replace("+aiosqlite", "")
    # Ensure it's postgresql:// for psycopg2
    if url.startswith("sqlite"):
        # For local sqlite, use sync sqlite
        url = url.replace("sqlite+aiosqlite", "sqlite")
    # Add sslmode for public PostgreSQL connections
    if url.startswith("postgresql://") and "sslmode" not in url:
        url = url + ("&" if "?" in url else "?") + "sslmode=require"
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_sync_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode with sync engine."""
    url = get_sync_url()
    connectable = create_engine(
        url,
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": 5},
    )

    # Retry connection with backoff for Railway internal DNS resolution
    max_retries = 5
    retry_delay = 2  # seconds

    for attempt in range(max_retries):
        try:
            with connectable.connect() as connection:
                context.configure(
                    connection=connection,
                    target_metadata=target_metadata,
                )

                with context.begin_transaction():
                    context.run_migrations()
            return  # Success, exit the retry loop
        except OperationalError as e:
            if attempt < max_retries - 1:
                print(f"Database connection failed (attempt {attempt + 1}/{max_retries}), retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                raise  # Re-raise on final attempt


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
