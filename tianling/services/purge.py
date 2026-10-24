"""Purge on member leave helper."""
from __future__ import annotations

from tianling.db.repositories.game_profiles import GameProfileRepository
from tianling.db.repositories.users import UserRepository


class DataPurgeService:
    def __init__(self, session):
        self.session = session
        self.users = UserRepository(session)
        self.profiles = GameProfileRepository(session)

    async def purge_member(self, guild_id: int, member_id: int):
        user = await self.users.get(guild_id, member_id)
        if not user:
            return
        from sqlalchemy import delete

        from tianling.db.models import GameProfile
        await self.session.execute(delete(GameProfile).where(GameProfile.user_id == user.id))
        await self.users.delete(user.id)
        await self.session.flush()
