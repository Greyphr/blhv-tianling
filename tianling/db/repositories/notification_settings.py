"""Notification settings repository."""
from __future__ import annotations

from sqlalchemy import select

from tianling.db.models import NotificationSetting
from tianling.db.repositories.base import BaseRepository


class NotificationSettingRepository(BaseRepository):
    async def get_for_user(self, user_id: int) -> NotificationSetting | None:
        stmt = select(NotificationSetting).where(NotificationSetting.user_id == user_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def set_opt_in(self, guild_id: int, user_id: int, opt_in: bool) -> NotificationSetting:
        existing = await self.get_for_user(user_id)
        if existing:
            existing.opt_in = opt_in
            await self.session.flush()
            return existing
        ns = NotificationSetting(guild_id=guild_id, user_id=user_id, opt_in=opt_in)
        self.session.add(ns)
        await self.session.flush()
        return ns
