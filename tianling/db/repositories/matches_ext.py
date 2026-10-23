"""Match repository list active."""
from __future__ import annotations

from sqlalchemy import select

from tianling.db.models import MatchGroup
from tianling.db.repositories.base import BaseRepository


class MatchGroupListMixin(BaseRepository):
    async def list_proposed(self, guild_id: int | None = None) -> list[MatchGroup]:
        stmt = select(MatchGroup).where(MatchGroup.status == "proposed")
        if guild_id is not None:
            stmt = stmt.where(MatchGroup.guild_id == guild_id)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())
