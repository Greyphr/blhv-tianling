"""Member penalty repository."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from tianling.db.models import MemberPenalty
from tianling.db.repositories.base import BaseRepository


class PenaltyRepository(BaseRepository):
    async def get_active(
        self, guild_id: int, member_id: int, now: datetime
    ) -> MemberPenalty | None:
        stmt = select(MemberPenalty).where(
            MemberPenalty.guild_id == guild_id,
            MemberPenalty.member_id == member_id,
            MemberPenalty.expires_at > now,
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def increment_and_set(
        self, guild_id: int, member_id: int, cooldown_seconds: int, now: datetime
    ) -> MemberPenalty:
        """Escalating cooldown: each repeat while active multiplies the duration."""
        res = await self.session.execute(
            select(MemberPenalty).where(
                MemberPenalty.guild_id == guild_id, MemberPenalty.member_id == member_id
            )
        )
        existing = res.scalar_one_or_none()
        if existing is not None:
            still_active = existing.expires_at > now
            existing.count = existing.count + 1 if still_active else 1
            existing.expires_at = now + timedelta(seconds=cooldown_seconds * existing.count)
            await self.session.flush()
            return existing
        penalty = MemberPenalty(
            guild_id=guild_id,
            member_id=member_id,
            count=1,
            expires_at=now + timedelta(seconds=cooldown_seconds),
        )
        self.session.add(penalty)
        await self.session.flush()
        return penalty
