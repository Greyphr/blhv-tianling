"""Tests for registry reconcile logic."""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tianling.db.base import Base
from tianling.db.repositories.game_profiles import GameProfileRepository
from tianling.db.repositories.users import UserRepository
from tianling.db.seed import seed_guild_data
from tianling.discord_ui.fake_adapter import FakeDiscordAdapter
from tianling.discord_ui.registry import RegistryService


@pytest.mark.asyncio
async def test_registry_reconcile_upsert_edit_delete(tmp_path):
    db_path = tmp_path / "reg.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        await seed_guild_data(session, 1)
        await session.commit()

    adapter = FakeDiscordAdapter()
    async with Session() as session:
        svc = RegistryService(session, adapter)
        urepo = UserRepository(session)
        user = await urepo.get_or_create(1, 555, display_name="Test")
        prep = GameProfileRepository(session)
        from tianling.db.models import GameProfile
        p = GameProfile(guild_id=1, user_id=user.id, game="marvel_rivals", ingame_name="TestPlayer", region="NA", current_rank_tier="Gold", current_division="2", current_queue_type="default", rank_source="self_reported")
        p = await prep.create(p)
        await session.commit()
        await svc.reconcile(1, 123456)
        assert len(adapter.messages) == 1
        mid = list(adapter.messages.keys())[0]
        # edit
        p.current_rank_tier = "Platinum"
        await session.commit()
        await svc.upsert_profile(1, p.id, 123456)
        assert adapter.messages[mid].embed.fields is not None
        # delete
        await svc.delete_profile(p.id)
        assert len(adapter.messages) == 0
    await engine.dispose()
