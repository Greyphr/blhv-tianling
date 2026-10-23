"""Member penalty repository."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from tianling.db.models import MemberPenalty
from tianling.db.repositories.base import BaseRepository


class PenaltyRepository(BaseRepository):
    async def get_active(self, guild_id: int, member_id: int, now: datetime) -> MemberPenalty | None:
        stmt = select(MemberPenalty).where(
            MemberPenalty.guild_id == guild_id,
            MemberPenalty.member_id == member_id,
            MemberPenalty.expires_at > now,
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def increment_and_set(self, guild_id: int, member_id: int, cooldown_seconds: int, now: datetime) -> MemberPenalty:
        existing = await self.get_active(guild_id, member_id, now)
        if existing:
            existing.count += 1
            existing.expires_at = now.replace(microsecond=0)
            from datetime import timedelta

            existing.expires_at = now + timedelta(seconds=cooldown_seconds * existing.count)
            await self.session.flush()
            return existing
        p = MemberPenalty(guild_id=guild_id, member_id=member_id, count=1, expires_at=now)
        from datetime import timedelta

        p.expires_at = now + timedelta(seconds=cooldown_seconds)
        self.session.add(p)
        await self.session.flush()
        return p
