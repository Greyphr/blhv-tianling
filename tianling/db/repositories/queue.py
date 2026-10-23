"""Queue repository with atomic reserve."""

from __future__ import annotations

from sqlalchemy import delete, select, update

from tianling.db.models import QueueEntry
from tianling.db.repositories.base import BaseRepository


class QueueRepository(BaseRepository):
    async def join(self, entry: QueueEntry) -> QueueEntry:
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def leave(self, guild_id: int, member_id: int, game: str, activity: str) -> bool:
        stmt = select(QueueEntry).where(
            QueueEntry.guild_id == guild_id,
            QueueEntry.member_id == member_id,
            QueueEntry.game == game,
            QueueEntry.activity == activity,
            QueueEntry.status.in_(["queued", "reserved", "proposed"]),
        )
        res = await self.session.execute(stmt)
        e = res.scalar_one_or_none()
        if not e:
            return False
        e.status = "cancelled"
        await self.session.flush()
        return True

    async def list_active(self, guild_id: int, game: str, activity: str) -> list[QueueEntry]:
        stmt = select(QueueEntry).where(
            QueueEntry.guild_id == guild_id,
            QueueEntry.game == game,
            QueueEntry.activity == activity,
            QueueEntry.status.in_(["queued", "reserved", "proposed"]),
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def list_for_user(
        self,
        guild_id: int,
        user_id: int,
        *,
        game: str | None = None,
        active_only: bool = False,
    ) -> list[QueueEntry]:
        conditions = [QueueEntry.guild_id == guild_id, QueueEntry.user_id == user_id]
        if game is not None:
            conditions.append(QueueEntry.game == game)
        if active_only:
            conditions.append(QueueEntry.status.in_(["queued", "reserved", "proposed"]))
        res = await self.session.execute(select(QueueEntry).where(*conditions))
        return list(res.scalars().all())

    async def party_ids_for_leader(self, guild_id: int, member_id: int) -> list[str]:
        stmt = select(QueueEntry.party_id).where(
            QueueEntry.guild_id == guild_id,
            QueueEntry.leader_member_id == member_id,
            QueueEntry.party_id.is_not(None),
        )
        res = await self.session.execute(stmt)
        return [party_id for party_id in res.scalars().all() if party_id]

    async def delete_for_user(self, guild_id: int, user_id: int, *, game: str | None = None) -> int:
        conditions = [QueueEntry.guild_id == guild_id, QueueEntry.user_id == user_id]
        if game is not None:
            conditions.append(QueueEntry.game == game)
        res = await self.session.execute(delete(QueueEntry).where(*conditions))
        return int(res.rowcount or 0)

    async def cancel_for_parties(self, guild_id: int, party_ids: list[str]) -> int:
        if not party_ids:
            return 0
        res = await self.session.execute(
            update(QueueEntry)
            .where(
                QueueEntry.guild_id == guild_id,
                QueueEntry.party_id.in_(party_ids),
                QueueEntry.status.in_(["queued", "reserved", "proposed"]),
            )
            .values(status="cancelled")
        )
        return int(res.rowcount or 0)

    async def reserve_entries(
        self, guild_id: int, party_id: str, entry_ids: list[int], leader_member_id: int
    ) -> list[QueueEntry]:
        """Atomically reserve queue entries for a proposed group in one transaction."""
        stmt = (
            update(QueueEntry)
            .where(
                QueueEntry.guild_id == guild_id,
                QueueEntry.id.in_(entry_ids),
                QueueEntry.status == "queued",
            )
            .values(
                status="reserved",
                party_id=party_id,
                party_size=len(entry_ids),
                leader_member_id=leader_member_id,
            )
        )
        res = await self.session.execute(stmt)
        if res.rowcount != len(entry_ids):
            raise ValueError("could not reserve all entries atomically")
        q = select(QueueEntry).where(QueueEntry.id.in_(entry_ids))
        res2 = await self.session.execute(q)
        return list(res2.scalars().all())
