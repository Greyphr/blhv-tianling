"""Recently failed roster hashes (TTL) so a failed group is not regenerated immediately."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select

from tianling.db.models import FailedRoster
from tianling.db.repositories.base import BaseRepository


class FailedRosterRepository(BaseRepository):
    async def add(
        self,
        guild_id: int,
        game: str,
        activity: str,
        roster_hash: str,
        now: datetime,
        ttl_seconds: int,
    ) -> FailedRoster:
        row = FailedRoster(
            guild_id=guild_id,
            game=game,
            activity=activity,
            roster_hash=roster_hash,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def active_hashes(
        self, guild_id: int, game: str, activity: str, now: datetime
    ) -> list[str]:
        res = await self.session.execute(
            select(FailedRoster.roster_hash).where(
                FailedRoster.guild_id == guild_id,
                FailedRoster.game == game,
                FailedRoster.activity == activity,
                FailedRoster.expires_at > now,
            )
        )
        return list(dict.fromkeys(res.scalars().all()))

    async def purge_expired(self, now: datetime) -> int:
        res = await self.session.execute(delete(FailedRoster).where(FailedRoster.expires_at <= now))
        return int(res.rowcount or 0)
