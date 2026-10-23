"""Blocked users repository."""
from __future__ import annotations

from sqlalchemy import select

from tianling.db.models import BlockedUser
from tianling.db.repositories.base import BaseRepository


class BlockedUserRepository(BaseRepository):
    async def is_blocked(self, guild_id: int, blocker_member_id: int, blocked_member_id: int) -> bool:
        stmt = select(BlockedUser).where(
            BlockedUser.guild_id == guild_id,
            BlockedUser.blocker_member_id == blocker_member_id,
            BlockedUser.blocked_member_id == blocked_member_id,
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none() is not None

    async def add(self, blocked: BlockedUser) -> BlockedUser:
        self.session.add(blocked)
        await self.session.flush()
        return blocked
