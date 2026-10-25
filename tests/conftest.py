"""Shared fixtures: a real file-backed SQLite DB built through the app's engine factory."""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tianling.db import models  # noqa: F401
from tianling.db.base import Base
from tianling.db.seed import seed_guild_data
from tianling.db.session import create_db_engine

GUILD = 1


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    """Session factory using the production engine setup (BEGIN IMMEDIATE, WAL, FK on)."""
    engine = create_db_engine(f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await seed_guild_data(session, GUILD)
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()
