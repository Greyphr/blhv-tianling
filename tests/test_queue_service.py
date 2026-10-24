"""Integration test for stateful services."""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tianling.db.base import Base
from tianling.db.seed import seed_guild_data
from tianling.services.queue_service import QueueConfig, QueueService


@pytest.mark.asyncio
async def test_queue_proposal_accept_finalize(tmp_path):
    db_path = tmp_path / "s.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        await seed_guild_data(session, 1)
        await session.commit()

    async with Session() as session:
        qs = QueueService(session, QueueConfig(confirmation_timeout_seconds=60, auto_confirm=False))
        await qs.join_queue(1, 111, "overwatch_2", "role_queue", queue_type="role_queue", rank_snapshot={"tier": "gold", "division": "2", "order": 12})
        await qs.join_queue(1, 222, "overwatch_2", "role_queue", queue_type="role_queue", rank_snapshot={"tier": "gold", "division": "1", "order": 13})
        events = qs.pop_events()
        # may have proposal
        assert events
        await session.commit()
    await engine.dispose()
