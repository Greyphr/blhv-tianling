"""Guild config repository."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from tianling.db.models import GuildConfig
from tianling.db.repositories.base import BaseRepository


class GuildConfigRepository(BaseRepository):
    async def get(self, guild_id: int, key: str) -> GuildConfig | None:
        stmt = select(GuildConfig).where(GuildConfig.guild_id == guild_id, GuildConfig.key == key)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_value(self, guild_id: int, key: str, default: Any = None) -> Any:
        row = await self.get(guild_id, key)
        if row is None or row.value is None:
            return default
        return row.value

    async def set(self, guild_id: int, key: str, value: dict | None) -> GuildConfig:
        existing = await self.get(guild_id, key)
        if existing:
            existing.value = value
            await self.session.flush()
            return existing
        gc = GuildConfig(guild_id=guild_id, key=key, value=value)
        self.session.add(gc)
        await self.session.flush()
        return gc

    async def update_value(self, guild_id: int, key: str, **changes: Any) -> dict:
        current = dict(await self.get_value(guild_id, key, {}) or {})
        current.update(changes)
        await self.set(guild_id, key, current)
        return current
