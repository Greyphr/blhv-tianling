"""Test mode helpers."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete

from tianling.db.models import QueueEntry
from tianling.db.repositories.users import UserRepository


class TestMode:
    def __init__(self, session, guild_id: int):
        self.session = session
        self.guild_id = guild_id
        self.users = UserRepository(session)

    async def add_player(self, member_id: int, game: str, activity: str, rank_snapshot: dict, role_preference: str | None = None, region: str | None = None):
        user = await self.users.get_or_create(self.guild_id, member_id)
        e = QueueEntry(
            guild_id=self.guild_id,
            user_id=user.id,
            member_id=member_id,
            game=game,
            activity=activity,
            queue_type="default",
            region=region,
            role_preference=role_preference,
            rank_snapshot=rank_snapshot,
            status="queued",
            queued_at=datetime.now(UTC),
            party_size=1,
            relaxation_level=0,
        )
        self.session.add(e)
        await self.session.flush()
        return e

    async def fill_queue(self, game: str, activity: str, count: int):
        for i in range(count):
            await self.add_player(
                900000 + i,
                game,
                activity,
                {"tier": "gold", "division": "1", "order": 12},
            )
        await self.session.flush()

    async def clear_all(self):
        from tianling.db.models import MatchGroup, MatchMember, PartyMember, QueueEntry

        await self.session.execute(delete(MatchMember).where(MatchMember.guild_id == self.guild_id))
        await self.session.execute(delete(MatchGroup).where(MatchGroup.guild_id == self.guild_id))
        await self.session.execute(delete(PartyMember).where(PartyMember.guild_id == self.guild_id))
        await self.session.execute(delete(QueueEntry).where(QueueEntry.guild_id == self.guild_id))
        await self.session.flush()
