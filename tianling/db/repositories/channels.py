"""Temporary channels repository."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update

from tianling.db.models import TemporaryChannel
from tianling.db.repositories.base import BaseRepository


class ChannelRepository(BaseRepository):
    async def create(self, ch: TemporaryChannel) -> TemporaryChannel:
        self.session.add(ch)
        await self.session.flush()
        return ch

    async def get_by_channel_id(self, channel_id: int) -> TemporaryChannel | None:
        stmt = select(TemporaryChannel).where(TemporaryChannel.channel_id == channel_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_empty_since(self, before: datetime) -> list[TemporaryChannel]:
        stmt = select(TemporaryChannel).where(
            TemporaryChannel.status == "active",
            TemporaryChannel.empty_since.is_not(None),
            TemporaryChannel.empty_since <= before,
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def mark_empty(self, channel_id: int, since: datetime) -> None:
        await self.session.execute(
            update(TemporaryChannel)
            .where(TemporaryChannel.channel_id == channel_id)
            .values(empty_since=since, status="empty")
        )

    async def mark_active(self, channel_id: int) -> None:
        await self.session.execute(
            update(TemporaryChannel)
            .where(TemporaryChannel.channel_id == channel_id)
            .values(empty_since=None, status="active")
        )
