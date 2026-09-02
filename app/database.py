"""SQLAlchemy async database setup."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

# Session factory
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Base class for ORM models."""

    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting database sessions."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create any missing tables on startup.

    NOTE — this is a convenience for fresh installs, NOT the schema source of
    truth. `create_all()` only CREATEs missing *tables*; it never ALTERs an
    existing one, so a new column added to a model appears on fresh databases
    and is silently absent on existing ones.

    **Alembic owns schema changes.** Adding or altering a column means writing
    a migration (`uv run alembic revision -m "..."`), not relying on this call.
    The one column that previously broke that rule — history.result — is now
    covered by migration a1c4e9f27b30.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Close database connections."""
    await engine.dispose()
