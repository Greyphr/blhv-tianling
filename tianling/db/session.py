"""Async session factory and DB helpers."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tianling.config import get_settings


def get_database_url() -> str:
    try:
        settings = get_settings()
        return settings.database_url
    except Exception:
        url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./data/tianling.db")
        return url


def ensure_sqlite_directory(url: str) -> None:
    if not url.startswith("sqlite"):
        return
    _, _, path = url.partition("///")
    if not path or path == ":memory:":
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def create_db_engine(url: str):
    ensure_sqlite_directory(url)
    db_engine = create_async_engine(url, echo=False)
    if db_engine.url.get_backend_name() == "sqlite":

        @event.listens_for(db_engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):  # type: ignore
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return db_engine


engine = create_db_engine(get_database_url())
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


class UnitOfWork:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()
