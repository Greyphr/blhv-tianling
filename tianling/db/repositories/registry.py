"""Registry repository."""
from __future__ import annotations

from sqlalchemy import delete, select

from tianling.db.models import RegistryMessage
from tianling.db.repositories.base import BaseRepository


class RegistryRepository(BaseRepository):
    async def get_by_profile(self, profile_id: int) -> RegistryMessage | None:
        stmt = select(RegistryMessage).where(RegistryMessage.profile_id == profile_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def create(self, rm: RegistryMessage) -> RegistryMessage:
        self.session.add(rm)
        await self.session.flush()
        return rm

    async def list_for_channel(self, channel_id: int) -> list[RegistryMessage]:
        stmt = select(RegistryMessage).where(RegistryMessage.channel_id == channel_id)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def delete_for_profile(self, profile_id: int) -> int:
        res = await self.session.execute(delete(RegistryMessage).where(RegistryMessage.profile_id == profile_id))
        return int(res.rowcount or 0)

    async def delete(self, rm_id: int) -> int:
        res = await self.session.execute(delete(RegistryMessage).where(RegistryMessage.id == rm_id))
        return int(res.rowcount or 0)
