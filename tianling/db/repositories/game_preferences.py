"""Per-game member preferences (free-form JSON)."""

from __future__ import annotations

from sqlalchemy import select

from tianling.db.models import GamePreference
from tianling.db.repositories.base import BaseRepository


class GamePreferenceRepository(BaseRepository):
    async def get(self, guild_id: int, user_id: int, game: str) -> GamePreference | None:
        res = await self.session.execute(
            select(GamePreference).where(
                GamePreference.guild_id == guild_id,
                GamePreference.user_id == user_id,
                GamePreference.game == game,
            )
        )
        return res.scalar_one_or_none()

    async def set(self, guild_id: int, user_id: int, game: str, data: dict) -> GamePreference:
        row = await self.get(guild_id, user_id, game)
        if row is None:
            row = GamePreference(guild_id=guild_id, user_id=user_id, game=game, data=data)
            self.session.add(row)
        else:
            row.data = data
        await self.session.flush()
        return row
