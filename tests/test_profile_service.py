"""Tests for profile CRUD, queue refreshes, cleanup, and stale profiles."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tianling.core.ranks import RankValidationError
from tianling.db.base import Base
from tianling.db.models import (
    GameProfile,
    MatchGroup,
    PartyMember,
    QueueEntry,
    RankDefinition,
    RegistryMessage,
    User,
)
from tianling.db.repositories.queue import QueueRepository
from tianling.db.repositories.users import UserRepository
from tianling.db.seed import seed_guild_data
from tianling.services.profile_service import (
    ProfileAlreadyExistsError,
    ProfileNotFoundError,
    ProfileService,
    ProfileValidationError,
)


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    db_path = tmp_path / "profiles.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        await seed_guild_data(session, 1)
        await session.commit()
    yield session_maker
    await engine.dispose()


def make_service(session: AsyncSession, **kwargs) -> ProfileService:
    kwargs.setdefault("stale_after_days", 30)
    return ProfileService(session, 1, **kwargs)


async def create_marvel_profile(
    service: ProfileService, user_id: int = 555, **overrides
) -> GameProfile:
    fields = {
        "ingame_name": "Nova",
        "region": "NA",
        "current_rank_tier": "Gold",
        "current_division": "II",
        "activity": "competitive",
        "preferred_roles": ["duelist"],
        "preferred_heroes": ["Hela"],
    }
    fields.update(overrides)
    return await service.create_profile(user_id, "marvel_rivals", **fields)


async def add_queue_entry(
    session: AsyncSession,
    profile: GameProfile,
    *,
    user_id: int | None = None,
    member_id: int = 555,
    party_id: str | None = None,
    status: str = "queued",
) -> QueueEntry:
    entry = QueueEntry(
        guild_id=1,
        user_id=user_id or profile.user_id,
        member_id=member_id,
        game=profile.game,
        activity=profile.activity or "competitive",
        queue_type=profile.current_queue_type,
        party_id=party_id,
        status=status,
        party_size=1,
        leader_member_id=member_id if party_id else None,
        rank_snapshot={"tier": "old"},
        relaxation_level=0,
    )
    session.add(entry)
    await session.flush()
    return entry


@pytest.mark.asyncio
async def test_create_profile_validates_and_does_not_queue(session_factory) -> None:
    async with session_factory() as session:
        service = make_service(session)
        profile = await create_marvel_profile(service)

        assert profile.id is not None
        assert profile.current_rank_tier == "Gold"
        assert profile.current_division == "II"
        assert profile.peak_rank_tier == "Gold"
        assert profile.current_is_unranked is False
        assert profile.preferred_roles == ["duelist"]
        assert profile.preferred_heroes == ["Hela"]
        assert profile.needs_update is False

        user = await UserRepository(session).get(1, 555)
        assert user is not None
        assert await QueueRepository(session).list_for_user(1, user.id) == []


@pytest.mark.asyncio
async def test_duplicate_profile_is_rejected(session_factory) -> None:
    async with session_factory() as session:
        service = make_service(session)
        await create_marvel_profile(service)
        with pytest.raises(ProfileAlreadyExistsError):
            await create_marvel_profile(service)
        count = await session.scalar(select(func.count()).select_from(GameProfile))
        assert count == 1


@pytest.mark.asyncio
async def test_invalid_rank_and_profile_fields_are_rejected(session_factory) -> None:
    async with session_factory() as session:
        service = make_service(session)
        with pytest.raises(RankValidationError):
            await create_marvel_profile(service, current_rank_tier="Mythic")
        with pytest.raises(RankValidationError):
            await create_marvel_profile(service, current_division="IV")
        with pytest.raises(RankValidationError):
            await create_marvel_profile(service, current_division=None)
        with pytest.raises(ProfileValidationError, match="unsupported game"):
            await service.create_profile(
                555, "not_a_game", current_rank_tier="Gold", current_division="II"
            )
        with pytest.raises(ProfileValidationError, match="invalid activity"):
            await create_marvel_profile(service, activity="raiding")
        with pytest.raises(ProfileValidationError, match="invalid region"):
            await create_marvel_profile(service, region="Mars")
        with pytest.raises(ProfileValidationError, match="invalid preferred_roles"):
            await create_marvel_profile(service, preferred_roles=["wizard"])
        with pytest.raises(ProfileValidationError, match="invalid preferred_heroes"):
            await create_marvel_profile(service, preferred_heroes=["Mario"])
        with pytest.raises(ProfileValidationError, match="unknown profile field"):
            await create_marvel_profile(service, favourite_colour="blue")
        with pytest.raises(ProfileValidationError, match="peak_rank_tier is required"):
            await create_marvel_profile(service, peak_division="I")
        assert await session.scalar(select(func.count()).select_from(GameProfile)) == 0
        assert await session.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.asyncio
async def test_leaderboard_ranks_are_valid_without_divisions(session_factory) -> None:
    async with session_factory() as session:
        service = make_service(session)
        ow = await service.create_profile(
            601,
            "overwatch_2",
            current_rank_tier="Top 500",
            current_queue_type="role_queue",
            activity="competitive",
        )
        apex = await service.create_profile(
            602,
            "apex_legends",
            current_rank_tier="Apex Predator",
            activity="ranked",
        )
        marvel = await service.create_profile(
            603,
            "marvel_rivals",
            current_rank_tier="Eternity",
            activity="competitive",
        )

        assert ow.current_division is None
        assert ow.current_queue_type == "role_queue"
        assert apex.current_division is None
        assert marvel.current_division is None
        assert ow.current_is_leaderboard_status is True
        assert apex.current_is_leaderboard_status is True
        assert marvel.current_is_leaderboard_status is True

        with pytest.raises(RankValidationError, match="no normal division"):
            await service.create_profile(
                604,
                "overwatch_2",
                current_rank_tier="Top 500",
                current_division="1",
                current_queue_type="role_queue",
            )


@pytest.mark.asyncio
async def test_update_profile_refreshes_queue_snapshot(session_factory) -> None:
    async with session_factory() as session:
        service = make_service(session)
        profile = await create_marvel_profile(service)
        entry = await add_queue_entry(session, profile)

        updated = await service.update_profile(
            555,
            "marvel_rivals",
            current_rank_tier="Diamond",
            current_division="I",
            ingame_name="Nova Prime",
        )
        assert updated.ingame_name == "Nova Prime"
        assert entry.status == "queued"
        assert entry.rank_snapshot is not None
        assert entry.rank_snapshot["tier"] == "Diamond"
        assert entry.rank_snapshot["division"] == "I"
        assert entry.rank_snapshot["queue_type"] == "default"


@pytest.mark.asyncio
async def test_queue_eligibility_hook_can_cancel_entry(session_factory) -> None:
    async with session_factory() as session:
        calls: list[tuple[int, int]] = []

        def deny(profile: GameProfile, entry: QueueEntry) -> bool:
            calls.append((profile.id, entry.id))
            return False

        service = make_service(session, eligibility_hook=deny)
        profile = await create_marvel_profile(service)
        entry = await add_queue_entry(session, profile)

        await service.update_profile(
            555, "marvel_rivals", current_rank_tier="Platinum", current_division="II"
        )
        assert calls == [(profile.id, entry.id)]
        assert entry.status == "cancelled"


@pytest.mark.asyncio
async def test_invalid_update_does_not_change_profile_or_queue(session_factory) -> None:
    async with session_factory() as session:
        service = make_service(session)
        profile = await create_marvel_profile(service)
        entry = await add_queue_entry(session, profile)

        with pytest.raises(RankValidationError):
            await service.update_profile(
                555, "marvel_rivals", current_rank_tier="Gold", current_division="IV"
            )
        assert profile.current_rank_tier == "Gold"
        assert profile.current_division == "II"
        assert entry.rank_snapshot == {"tier": "old"}


@pytest.mark.asyncio
async def test_get_and_delete_profile_clean_registry_and_queues(session_factory) -> None:
    async with session_factory() as session:
        service = make_service(session)
        profile = await create_marvel_profile(service)
        session.add(
            RegistryMessage(guild_id=1, profile_id=profile.id, channel_id=10, message_id=11)
        )
        await add_queue_entry(session, profile)
        await session.flush()

        loaded = await service.get_profile(555, "marvel_rivals")
        assert loaded is not None and loaded.id == profile.id

        assert await service.delete_profile(555, "marvel_rivals") is True
        assert await service.delete_profile(555, "marvel_rivals") is False
        assert await session.scalar(select(func.count()).select_from(GameProfile)) == 0
        assert await session.scalar(select(func.count()).select_from(RegistryMessage)) == 0
        assert await session.scalar(select(func.count()).select_from(QueueEntry)) == 0


@pytest.mark.asyncio
async def test_delete_profile_cancels_active_party(session_factory) -> None:
    async with session_factory() as session:
        service = make_service(session)
        profile = await create_marvel_profile(service)
        own_entry = await add_queue_entry(session, profile, party_id="party-1")

        other = await UserRepository(session).get_or_create(1, 777)
        other_entry = QueueEntry(
            guild_id=1,
            user_id=other.id,
            member_id=777,
            game="marvel_rivals",
            activity="competitive",
            queue_type="default",
            party_id="party-1",
            status="reserved",
            party_size=2,
            leader_member_id=555,
            relaxation_level=0,
        )
        session.add(other_entry)
        session.add_all(
            [
                PartyMember(guild_id=1, party_id="party-1", member_id=555),
                PartyMember(guild_id=1, party_id="party-1", member_id=777),
                MatchGroup(
                    guild_id=1,
                    group_uuid="group-1",
                    game="marvel_rivals",
                    activity="competitive",
                    queue_type="default",
                    party_id="party-1",
                    status="proposed",
                    proposal_status="pending",
                ),
            ]
        )
        await session.flush()
        other_entry_id = other_entry.id
        own_entry_id = own_entry.id

        assert await service.delete_profile(555, "marvel_rivals") is True
        session.expire_all()

        assert await session.get(QueueEntry, own_entry_id) is None
        refreshed = await session.get(QueueEntry, other_entry_id)
        assert refreshed is not None and refreshed.status == "cancelled"
        assert await session.scalar(select(func.count()).select_from(PartyMember)) == 0
        group = await session.scalar(select(MatchGroup).where(MatchGroup.group_uuid == "group-1"))
        assert group is not None and group.status == "cancelled"
        assert await session.scalar(select(func.count()).select_from(User)) == 2


@pytest.mark.asyncio
async def test_update_missing_profile_raises(session_factory) -> None:
    async with session_factory() as session:
        service = make_service(session)
        with pytest.raises(ProfileNotFoundError):
            await service.update_profile(999, "marvel_rivals", ingame_name="Nobody")


@pytest.mark.asyncio
async def test_delete_all_user_data_removes_everything_and_cancels_parties(
    session_factory,
) -> None:
    async with session_factory() as session:
        service = make_service(session)
        marvel = await create_marvel_profile(service)
        apex = await service.create_profile(
            555,
            "apex_legends",
            current_rank_tier="Diamond",
            current_division="III",
            activity="ranked",
        )
        session.add_all(
            [
                RegistryMessage(guild_id=1, profile_id=marvel.id, channel_id=10, message_id=11),
                RegistryMessage(guild_id=1, profile_id=apex.id, channel_id=10, message_id=12),
            ]
        )
        own_entry = await add_queue_entry(session, marvel, party_id="party-1")

        other = await UserRepository(session).get_or_create(1, 777)
        other_entry = QueueEntry(
            guild_id=1,
            user_id=other.id,
            member_id=777,
            game="marvel_rivals",
            activity="competitive",
            queue_type="default",
            party_id="party-1",
            status="reserved",
            party_size=2,
            leader_member_id=555,
            relaxation_level=0,
        )
        session.add(other_entry)
        session.add_all(
            [
                PartyMember(guild_id=1, party_id="party-1", member_id=555),
                PartyMember(guild_id=1, party_id="party-1", member_id=777),
                MatchGroup(
                    guild_id=1,
                    group_uuid="group-1",
                    game="marvel_rivals",
                    activity="competitive",
                    queue_type="default",
                    party_id="party-1",
                    status="proposed",
                    proposal_status="pending",
                ),
            ]
        )
        await session.flush()
        other_entry_id = other_entry.id

        summary = await service.delete_all_user_data(555)
        session.expire_all()

        assert summary.profiles == 2
        assert summary.registry_entries == 2
        assert summary.queue_entries == 1
        assert summary.parties_cancelled == 1
        assert await session.scalar(select(func.count()).select_from(GameProfile)) == 0
        assert await session.scalar(select(func.count()).select_from(RegistryMessage)) == 0
        assert await session.scalar(select(func.count()).select_from(PartyMember)) == 0
        assert (
            await session.scalar(
                select(func.count()).select_from(User).where(User.member_id == 555)
            )
            == 0
        )
        refreshed = await session.get(QueueEntry, other_entry_id)
        assert refreshed is not None and refreshed.status == "cancelled"
        group = await session.scalar(select(MatchGroup).where(MatchGroup.group_uuid == "group-1"))
        assert group is not None
        assert group.status == "cancelled"
        assert group.proposal_status == "cancelled"
        assert own_entry.id is not None


@pytest.mark.asyncio
async def test_on_member_leave_is_an_alias_for_cleanup(session_factory) -> None:
    async with session_factory() as session:
        service = make_service(session)
        await create_marvel_profile(service)
        summary = await service.on_member_leave(555)
        assert summary.profiles == 1
        assert await session.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.asyncio
async def test_stale_profiles_use_configured_days(session_factory) -> None:
    async with session_factory() as session:
        service = make_service(session, stale_after_days=30)
        stale = await create_marvel_profile(service)
        fresh = await service.create_profile(
            555,
            "apex_legends",
            current_rank_tier="Diamond",
            current_division="III",
            activity="ranked",
        )
        now = datetime.now(UTC)
        stale.updated_at = now - timedelta(days=31)
        fresh.updated_at = now - timedelta(days=1)
        await session.flush()

        found = await service.stale_profiles(now=now)
        assert [profile.id for profile in found] == [stale.id]
        assert [profile.id for profile in await service.stale_profiles(days=15, now=now)] == [
            stale.id
        ]
        assert await service.stale_profiles(days=60, now=now) == []
        with pytest.raises(ProfileValidationError):
            await service.stale_profiles(days=-1, now=now)


@pytest.mark.asyncio
async def test_invalid_ranks_after_admin_edit_mark_profile_needs_update(
    session_factory,
) -> None:
    async with session_factory() as session:
        service = make_service(session)
        await create_marvel_profile(service)
        await session.execute(
            delete(RankDefinition).where(
                RankDefinition.guild_id == 1,
                RankDefinition.game == "marvel_rivals",
                RankDefinition.queue_type == "default",
                RankDefinition.rank_tier == "Gold",
                RankDefinition.division == "II",
            )
        )
        await session.flush()

        loaded = await service.get_profile(555, "marvel_rivals")
        assert loaded is not None
        assert loaded.needs_update is True
        assert loaded.needs_update_reason is not None
        assert "Gold" in loaded.needs_update_reason


@pytest.mark.asyncio
async def test_mark_invalid_profiles_scans_guild(session_factory) -> None:
    async with session_factory() as session:
        service = make_service(session)
        await create_marvel_profile(service)
        await session.execute(
            delete(RankDefinition).where(
                RankDefinition.guild_id == 1,
                RankDefinition.game == "marvel_rivals",
                RankDefinition.queue_type == "default",
                RankDefinition.rank_tier == "Gold",
                RankDefinition.division == "II",
            )
        )
        await session.flush()

        assert await service.mark_invalid_profiles() == 1
        profile = await session.scalar(select(GameProfile))
        assert profile is not None and profile.needs_update is True
