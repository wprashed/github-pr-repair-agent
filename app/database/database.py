"""Database connection and session management."""

from contextlib import asynccontextmanager, contextmanager
from typing import AsyncGenerator, Generator
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import settings
from app.database.models import Base

# Sync engine & sessionmaker (for CLI, synchronous scripts, tests)
sync_engine = create_engine(
    settings.SQLITE_SYNC_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)
SyncSessionLocal = sessionmaker(bind=sync_engine, autocommit=False, autoflush=False)

# Async engine & sessionmaker (for FastAPI, background async tasks)
async_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


def init_db() -> None:
    """Initialize SQLite database tables synchronously."""
    settings.ensure_directories()
    Base.metadata.create_all(bind=sync_engine)


async def async_init_db() -> None:
    """Initialize SQLite database tables asynchronously."""
    settings.ensure_directories()
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@contextmanager
def get_sync_db() -> Generator[Session, None, None]:
    """Provide a transactional synchronous database session."""
    session = SyncSessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@asynccontextmanager
async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional asynchronous database session for context manager usage."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for async database sessions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
