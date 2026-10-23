"""User repository."""

from __future__ import annotations

from sqlalchemy import delete, select

from tianling.db.models import User
from tianling.db.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    async def get(self, guild_id: int, member_id: int) -> User | None:
        stmt = select(User).where(User.guild_id == guild_id, User.member_id == member_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def delete(self, user_id: int) -> bool:
        res = await self.session.execute(delete(User).where(User.id == user_id))
        return bool(res.rowcount)

    async def get_or_create(
        self, guild_id: int, member_id: int, display_name: str | None = None
    ) -> User:
        stmt = select(User).where(User.guild_id == guild_id, User.member_id == member_id)
        res = await self.session.execute(stmt)
        user = res.scalar_one_or_none()
        if user:
            if display_name and user.display_name != display_name:
                user.display_name = display_name
            return user
        user = User(guild_id=guild_id, member_id=member_id, display_name=display_name)
        self.session.add(user)
        await self.session.flush()
        return user

    async def get_by_id(self, user_id: int) -> User | None:
        res = await self.session.execute(select(User).where(User.id == user_id))
        return res.scalar_one_or_none()
