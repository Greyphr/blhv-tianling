"""Repository and migration tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tianling.db import models  # noqa: F401
from tianling.db.base import Base
from tianling.db.models import Composition, GameProfile, QueueEntry, RankDefinition
from tianling.db.repositories.game_profiles import GameProfileRepository
from tianling.db.repositories.queue import QueueRepository
from tianling.db.repositories.users import UserRepository
from tianling.db.seed import seed_guild_data


def test_migrations_run_from_empty_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from alembic.config import Config

    from alembic import command

    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    command.upgrade(cfg, "head")
    assert db_path.exists()

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
        profile_columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(game_profiles)")).fetchall()
        }
        revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    engine.dispose()
    tables = {r[0] for r in rows}
    required = {
        "users",
        "game_profiles",
        "rank_definitions",
        "game_preferences",
        "notification_settings",
        "queue_entries",
        "party_members",
        "match_groups",
        "match_members",
        "temporary_channels",
        "guild_config",
        "audit_logs",
        "blocked_users",
        "registry_messages",
        "scheduled_jobs",
        "compositions",
    }
    assert required <= tables
    assert {"needs_update", "needs_update_reason"} <= profile_columns
    assert revision == "0003_member_penalties"


@pytest.mark.asyncio
async def test_duplicate_profile_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "p.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA foreign_keys=ON"))
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        urepo = UserRepository(session)
        user = await urepo.get_or_create(1, 111)
        prep = GameProfileRepository(session)
        p1 = GameProfile(guild_id=1, user_id=user.id, game="marvel_rivals", ingame_name="a")
        await prep.create(p1)
        await session.commit()
        p2 = GameProfile(guild_id=1, user_id=user.id, game="marvel_rivals", ingame_name="b")
        session.add(p2)
        with pytest.raises(IntegrityError):
            await session.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_active_queue_entry_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "q.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA foreign_keys=ON"))
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        urepo = UserRepository(session)
        user = await urepo.get_or_create(1, 222)
        qrepo = QueueRepository(session)
        e1 = QueueEntry(
            guild_id=1,
            user_id=user.id,
            member_id=222,
            game="marvel_rivals",
            activity="competitive",
            queue_type="default",
            status="queued",
            queued_at=datetime.now(UTC),
            party_size=1,
            relaxation_level=0,
        )
        await qrepo.join(e1)
        await session.commit()
        e2 = QueueEntry(
            guild_id=1,
            user_id=user.id,
            member_id=222,
            game="marvel_rivals",
            activity="competitive",
            queue_type="default",
            status="queued",
            queued_at=datetime.now(UTC),
            party_size=1,
            relaxation_level=0,
        )
        session.add(e2)
        with pytest.raises(IntegrityError):
            await session.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_reserve_entries_atomic_under_concurrent_tasks(tmp_path: Path) -> None:
    db_path = tmp_path / "r.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA foreign_keys=ON"))
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as session:
        urepo = UserRepository(session)
        u1 = await urepo.get_or_create(1, 333)
        u2 = await urepo.get_or_create(1, 444)
        qrepo = QueueRepository(session)
        e1 = QueueEntry(
            guild_id=1,
            user_id=u1.id,
            member_id=333,
            game="overwatch_2",
            activity="role_queue",
            queue_type="role_queue",
            status="queued",
            queued_at=datetime.now(UTC),
            party_size=1,
            relaxation_level=0,
        )
        e2 = QueueEntry(
            guild_id=1,
            user_id=u2.id,
            member_id=444,
            game="overwatch_2",
            activity="role_queue",
            queue_type="role_queue",
            status="queued",
            queued_at=datetime.now(UTC),
            party_size=1,
            relaxation_level=0,
        )
        await qrepo.join(e1)
        await qrepo.join(e2)
        await session.commit()
        ids = [e1.id, e2.id]

    async def try_reserve(session_maker, pid):
        async with session_maker() as s:
            q = QueueRepository(s)
            try:
                await q.reserve_entries(1, pid, ids, 333)
                await s.commit()
                return "ok"
            except Exception:
                await s.rollback()
                return "bad"

    results = await asyncio.gather(*[try_reserve(Session, "p1"), try_reserve(Session, "p2")])
    assert results.count("ok") == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_seed_upsert_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "seed.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as session:
        await seed_guild_data(session, 1)
        await session.commit()

    async with Session() as session:
        await seed_guild_data(session, 1)
        await session.commit()
        marvel_ranks = await session.scalar(
            select(func.count())
            .select_from(RankDefinition)
            .where(
                RankDefinition.guild_id == 1,
                RankDefinition.game == "marvel_rivals",
                RankDefinition.queue_type == "default",
            )
        )
        comps = await session.scalar(
            select(func.count()).select_from(Composition).where(Composition.guild_id == 1)
        )
    await engine.dispose()
    assert marvel_ranks == 23
    assert comps == 9


@pytest.mark.asyncio
async def test_sqlite_pragmas(tmp_path: Path) -> None:
    from tianling.db.session import create_db_engine

    db_path = tmp_path / "pragma.db"
    engine = create_db_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    async with engine.connect() as conn:
        foreign_keys = await conn.scalar(text("PRAGMA foreign_keys"))
        journal_mode = await conn.scalar(text("PRAGMA journal_mode"))
    await engine.dispose()
    assert foreign_keys == 1
    assert journal_mode.lower() == "wal"
