"""Party member repository."""

from __future__ import annotations

from sqlalchemy import delete, select, update

from tianling.db.models import PartyMember
from tianling.db.repositories.base import BaseRepository


class PartyRepository(BaseRepository):
    async def add_member(self, pm: PartyMember) -> PartyMember:
        self.session.add(pm)
        await self.session.flush()
        return pm

    async def list_for_party(self, guild_id: int, party_id: str) -> list[PartyMember]:
        stmt = (
            select(PartyMember)
            .where(PartyMember.guild_id == guild_id, PartyMember.party_id == party_id)
            .order_by(PartyMember.id.asc())
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def list_for_parties(
        self, guild_id: int, party_ids: list[str]
    ) -> dict[str, list[PartyMember]]:
        grouped: dict[str, list[PartyMember]] = {pid: [] for pid in party_ids}
        if not party_ids:
            return grouped
        res = await self.session.execute(
            select(PartyMember)
            .where(PartyMember.guild_id == guild_id, PartyMember.party_id.in_(party_ids))
            .order_by(PartyMember.id.asc())
        )
        for pm in res.scalars().all():
            grouped.setdefault(pm.party_id, []).append(pm)
        return grouped

    async def party_ids_for_member(self, guild_id: int, member_id: int) -> list[str]:
        stmt = select(PartyMember.party_id).where(
            PartyMember.guild_id == guild_id, PartyMember.member_id == member_id
        )
        res = await self.session.execute(stmt)
        return list(dict.fromkeys(res.scalars().all()))

    async def delete_for_parties(self, guild_id: int, party_ids: list[str]) -> int:
        if not party_ids:
            return 0
        res = await self.session.execute(
            delete(PartyMember).where(
                PartyMember.guild_id == guild_id, PartyMember.party_id.in_(party_ids)
            )
        )
        return int(res.rowcount or 0)

    async def delete_for_member(self, guild_id: int, member_id: int) -> int:
        res = await self.session.execute(
            delete(PartyMember).where(
                PartyMember.guild_id == guild_id, PartyMember.member_id == member_id
            )
        )
        return int(res.rowcount or 0)

    async def remove_member(self, guild_id: int, party_id: str, member_id: int) -> int:
        res = await self.session.execute(
            delete(PartyMember).where(
                PartyMember.guild_id == guild_id,
                PartyMember.party_id == party_id,
                PartyMember.member_id == member_id,
            )
        )
        return int(res.rowcount or 0)

    async def update_member(
        self, guild_id: int, party_id: str, member_id: int, **values
    ) -> PartyMember | None:
        res = await self.session.execute(
            update(PartyMember)
            .where(
                PartyMember.guild_id == guild_id,
                PartyMember.party_id == party_id,
                PartyMember.member_id == member_id,
            )
            .values(**values)
        )
        if res.rowcount == 0:
            return None
        q = select(PartyMember).where(
            PartyMember.guild_id == guild_id,
            PartyMember.party_id == party_id,
            PartyMember.member_id == member_id,
        )
        r2 = await self.session.execute(q)
        return r2.scalar_one_or_none()
