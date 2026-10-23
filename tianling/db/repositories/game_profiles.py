"""Game profile repository."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select

from tianling.db.models import GameProfile
from tianling.db.repositories.base import BaseRepository


class GameProfileRepository(BaseRepository):
    async def create(self, profile: GameProfile) -> GameProfile:
        self.session.add(profile)
        await self.session.flush()
        return profile

    async def get(self, guild_id: int, user_id: int, game: str) -> GameProfile | None:
        stmt = select(GameProfile).where(
            GameProfile.guild_id == guild_id,
            GameProfile.user_id == user_id,
            GameProfile.game == game,
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_for_user(self, guild_id: int, user_id: int) -> list[GameProfile]:
        stmt = select(GameProfile).where(
            GameProfile.guild_id == guild_id, GameProfile.user_id == user_id
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_by_id(self, profile_id: int) -> GameProfile | None:
        res = await self.session.execute(select(GameProfile).where(GameProfile.id == profile_id))
        return res.scalar_one_or_none()

    async def list_for_guild(self, guild_id: int) -> list[GameProfile]:
        stmt = select(GameProfile).where(GameProfile.guild_id == guild_id)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def list_updated_before(self, guild_id: int, before: datetime) -> list[GameProfile]:
        stmt = select(GameProfile).where(
            GameProfile.guild_id == guild_id, GameProfile.updated_at < before
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def delete(self, guild_id: int, user_id: int, game: str) -> bool:
        stmt = delete(GameProfile).where(
            GameProfile.guild_id == guild_id,
            GameProfile.user_id == user_id,
            GameProfile.game == game,
        )
        res = await self.session.execute(stmt)
        return res.rowcount > 0
