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
            # Take manual control of transactions so BEGIN IMMEDIATE can be used.
            dbapi_connection.isolation_level = None
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

        @event.listens_for(db_engine.sync_engine, "begin")
        def _begin_immediate(conn):  # type: ignore
            # Writers serialize at transaction start instead of failing with
            # SQLITE_BUSY_SNAPSHOT halfway through a read-then-write sequence.
            # This is what makes concurrent queue joins safe on SQLite.
            conn.exec_driver_sql("BEGIN IMMEDIATE")

    return db_engine


engine = create_db_engine(get_database_url())
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transaction boundary. Handlers own commits; services only flush.

    Never hold a session open across Discord API calls and never open a second
    ``session_scope`` while one is active: on SQLite the second one would wait
    for the first one's write lock.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def make_session_scope(factory: async_sessionmaker[AsyncSession]):
    """Build a ``session_scope``-style context manager bound to another session factory."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    return scope


class UnitOfWork:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()
